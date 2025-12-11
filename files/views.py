import uuid
from rest_framework import views, status, serializers
from rest_framework.response import Response
from django.db import connections
from django.contrib.auth import get_user_model
from django.conf import settings
import copy
import pandas as pd
import io
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from accounts.models import Account, AccountUser
from files.models import FileUpload
from files.tasks import process_verification_pipeline
from core.redis_utils import add_to_list, delete_from_list # Import new list management functions

User = get_user_model()

# --- Utility Functions ---

def get_user_and_account_data_from_request(request):
    """
    Extracts user and account context using the Authorization header.
    """
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return None, None
    
    token = auth_header.split(' ')[1]
    
    try:
        # The token is structured as access_USERID_timestamp
        user_id_str = token.split('_')[1]
        user_id = int(user_id_str)
    except (IndexError, ValueError):
        return None, None
    
    try:
        user = User.objects.get(pk=user_id)
        account = user.account
        
        if not user.is_active or (user.role != 'ADMIN' and not account):
            return None, None
            
        return user, account
    except User.DoesNotExist:
        return None, None

def configure_account_db(account_db_name):
    """
    Dynamically registers the account database configuration using a deep copy
    of the default settings to ensure all internal Django keys are present.
    (Fixes KeyError: 'ATOMIC_REQUESTS' and 'TIME_ZONE')
    """
    if account_db_name not in connections.databases:
        try:
            default_config = copy.deepcopy(settings.DATABASES['default'])
        except KeyError as e:
            print(f"FATAL CONFIG ERROR: Default database configuration missing: {e}")
            return False

        default_config['NAME'] = account_db_name
        connections.databases[account_db_name] = default_config
        connections.close_all()
        return True
    return True

# --- Serializers ---

class FileListSerializer(serializers.ModelSerializer):
    """Serializer for FileUpload list display."""
    
    started_at = serializers.DateTimeField(format="%Y-%m-%d %H:%M:%S", read_only=True)
    completed_at = serializers.DateTimeField(format="%Y-%m-%d %H:%M:%S", read_only=True)
    
    class Meta:
        model = FileUpload
        fields = (
            'file_id', 'file_name', 'status', 'original_record_count', 
            'unique_record_count', 'filtered_unsub_count', 
            'filtered_bounce_count', 'started_at', 'completed_at'
        )

# --- API Views ---

@method_decorator(csrf_exempt, name='dispatch')
class FileUploadView(views.APIView):
    # Disable session-based authentication for these API endpoints so CSRF is not required
    authentication_classes = []
    permission_classes = [] 

    def post(self, request, *args, **kwargs):
        user, account = get_user_and_account_data_from_request(request)
        
        if not account:
            return Response({"detail": "Authentication credentials were not provided or are invalid."}, status=status.HTTP_401_UNAUTHORIZED)
        
        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            return Response({"detail": "No file provided."}, status=status.HTTP_400_BAD_REQUEST)

        if uploaded_file.size > 50 * 1024 * 1024:
            return Response({"detail": "File too large (Max 50MB)."}, status=status.HTTP_413_PAYLOAD_TOO_LARGE)
        
        account_db_name = account.database_name
        if not configure_account_db(account_db_name):
             return Response({"detail": "Failed to configure account database."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        file_id = str(uuid.uuid4())
        
        try:
            file_upload = FileUpload.objects.using(account_db_name).create(
                file_id=file_id,
                file_name=uploaded_file.name,
                file_path=uploaded_file, 
                uploaded_by_user_id=user.pk,
                original_record_count=0, 
                status='UPLOADED'
            )
        except Exception as e:
            print(f"Database write error to {account_db_name}: {e}")
            return Response({"detail": "Failed to write metadata to account database."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        process_verification_pipeline.delay(file_id, account.account_id)
        
        serializer = FileListSerializer(file_upload)
        return Response(serializer.data, status=status.HTTP_202_ACCEPTED)

@method_decorator(csrf_exempt, name='dispatch')
class FileStatusView(views.APIView):
    authentication_classes = []
    permission_classes = [] 

    def get(self, request, file_id, *args, **kwargs):
        user, account = get_user_and_account_data_from_request(request)
        
        if not account:
            return Response({"detail": "Authentication failed."}, status=status.HTTP_401_UNAUTHORIZED)
        
        account_db_name = account.database_name
        
        if not configure_account_db(account_db_name):
             return Response({"detail": "Failed to configure account database."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        try:
            upload = FileUpload.objects.using(account_db_name).get(file_id=file_id)
            
            serializer = FileListSerializer(upload)
            return Response(serializer.data, status=status.HTTP_200_OK)
            
        except FileUpload.DoesNotExist:
            return Response({"detail": "File not found."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            print(f"Error in FileStatusView for {file_id}: {e}")
            return Response({"detail": "Internal server error during status retrieval."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@method_decorator(csrf_exempt, name='dispatch')
class FileListView(views.APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, *args, **kwargs):
        user, account = get_user_and_account_data_from_request(request)
        
        if not account:
            return Response({"detail": "Authentication failed."}, status=status.HTTP_401_UNAUTHORIZED)
        
        account_db_name = account.database_name
        
        if not configure_account_db(account_db_name):
             return Response({"detail": "Failed to configure account database."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        try:
            uploads = FileUpload.objects.using(account_db_name).all().order_by('-file_id')[:20] 
            
            serializer = FileListSerializer(uploads, many=True)
            return Response({"files": serializer.data}, status=status.HTTP_200_OK)

        except Exception as e:
            print(f"Error fetching file list for {account_db_name}: {e}")
            return Response({"detail": "Failed to retrieve upload history. Ensure database is provisioned."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@method_decorator(csrf_exempt, name='dispatch')
class ListUploadView(views.APIView):
    """
    Handles POST /v1/lists/upload/bounce and /unsub (FR14, FR17)
    """
    authentication_classes = []
    permission_classes = []
    
    def post(self, request, list_type, *args, **kwargs):
        user, account = get_user_and_account_data_from_request(request)
        if not account:
            return Response({"detail": "Authentication failed."}, status=status.HTTP_401_UNAUTHORIZED)

        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            return Response({"detail": "No file provided."}, status=status.HTTP_400_BAD_REQUEST)
        
        if list_type not in ['bounce', 'unsub']:
            return Response({"detail": "Invalid list type specified."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Read CSV content from memory
            content = uploaded_file.read().decode('utf-8')
            df = pd.read_csv(io.StringIO(content))
            
            if df.empty:
                return Response({"detail": "File is empty."}, status=status.HTTP_400_BAD_REQUEST)

            emails = df.iloc[:, 0].dropna().str.lower().unique()
            
            added_count = 0
            
            # Use Redis utility for fast addition (O(1))
            for email in emails:
                if add_to_list(account.account_id, email, list_type=list_type.upper()):
                    added_count += 1
            
            return Response({
                "list_type": list_type,
                "total_emails_in_file": len(emails),
                "emails_added": added_count,
                "emails_skipped": len(emails) - added_count,
                "message": f"Successfully uploaded and merged {list_type} list."
            }, status=status.HTTP_200_OK)

        except Exception as e:
            print(f"Error processing list upload ({list_type}): {e}")
            return Response({"detail": f"Failed to process list file: {e}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@method_decorator(csrf_exempt, name='dispatch')
class ListDeleteView(views.APIView):
    """
    Handles DELETE /v1/lists/{list_type}/{email} (FR7.4)
    """
    authentication_classes = []
    permission_classes = []
    
    def delete(self, request, list_type, email, *args, **kwargs):
        user, account = get_user_and_account_data_from_request(request)
        if not account:
            return Response({"detail": "Authentication failed."}, status=status.HTTP_401_UNAUTHORIZED)

        if list_type not in ['bounce', 'unsub']:
            return Response({"detail": "Invalid list type specified."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Use Redis utility to delete the entry
            if delete_from_list(account.account_id, email.lower(), list_type=list_type.upper()):
                return Response({"message": f"Successfully deleted '{email}' from {list_type} list."}, status=status.HTTP_200_OK)
            else:
                return Response({"message": f"Email '{email}' not found in {list_type} list."}, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            print(f"Error deleting from list ({list_type}): {e}")
            return Response({"detail": f"Failed to delete email: {e}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)