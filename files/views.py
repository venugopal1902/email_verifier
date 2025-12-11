import uuid
from rest_framework import views, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db import connections
from django.contrib.auth import get_user_model
from django.conf import settings # Import settings to access DATABASES
import copy # Import copy for deep copy operations

from accounts.models import Account, AccountUser
from files.models import FileUpload
from files.tasks import process_verification_pipeline

User = get_user_model()

# --- Utility Function to Extract User Context ---
def get_user_and_account_data_from_request(request):
    """
    Extracts user and account context using the Authorization header.
    """
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return None, None
    
    # Extract the simulated JWT (e.g., access_1_1702202000.0)
    token = auth_header.split(' ')[1]
    
    # Find the user ID embedded in the token (e.g., '1' from 'access_1_...')
    try:
        user_id_str = token.split('_')[1]
        user_id = int(user_id_str)
    except (IndexError, ValueError):
        # Invalid token format
        return None, None
    
    try:
        # Look up the user by the ID found in the token
        user = User.objects.get(pk=user_id)
        account = user.account
        
        # Security check: Ensure user is active and linked to an account (if not admin)
        if not user.is_active or (user.role != 'ADMIN' and not account):
            return None, None
            
        return user, account
    except User.DoesNotExist:
        return None, None

# --- Utility Function to Dynamically Configure Account DB ---
def configure_account_db(account_db_name):
    """
    Dynamically registers the account database configuration using a deep copy
    of the default settings to ensure all internal Django keys are present.
    """
    if account_db_name not in connections.databases:
        # Get a deep copy of the default configuration
        try:
            default_config = copy.deepcopy(settings.DATABASES['default'])
        except KeyError as e:
            # Should not happen if settings.py is correct
            print(f"FATAL CONFIG ERROR: Default database configuration missing: {e}")
            return False

        # Override only the NAME to point to the isolated account database
        default_config['NAME'] = account_db_name
        
        # Register the new database configuration
        connections.databases[account_db_name] = default_config
        
        # Force connections to reload to avoid stale configuration caches
        connections.close_all()
        return True
    return True


class FileUploadView(views.APIView):
    """
    Handles POST /v1/files/upload (FR10)
    """
    permission_classes = [] 

    def post(self, request, *args, **kwargs):
        # 1. Get Context
        user, account = get_user_and_account_data_from_request(request)
        
        if not account:
            return Response({"detail": "Authentication credentials were not provided or are invalid."}, status=status.HTTP_401_UNAUTHORIZED)
        
        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            return Response({"detail": "No file provided."}, status=status.HTTP_400_BAD_REQUEST)

        # 2. Basic Validation
        if uploaded_file.size > 50 * 1024 * 1024:
            return Response({"detail": "File too large (Max 50MB)."}, status=status.HTTP_413_PAYLOAD_TOO_LARGE)
        
        # 3. Dynamic DB Configuration (Fixes KeyError: 'ATOMIC_REQUESTS' and 'TIME_ZONE')
        account_db_name = account.database_name
        if not configure_account_db(account_db_name):
             return Response({"detail": "Failed to configure account database."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # 4. Create FileUpload Entry in Account DB
        file_id = str(uuid.uuid4())
        
        try:
            # Use the dynamically registered database connection
            file_upload = FileUpload.objects.using(account_db_name).create(
                file_id=file_id,
                file_name=uploaded_file.name,
                file_path=uploaded_file, 
                uploaded_by_user_id=user.pk,
                original_record_count=0, 
                status='UPLOADED'
            )
        except Exception as e:
            # Catch database write errors and log the traceback
            print(f"Database write error to {account_db_name}: {e}")
            return Response({"detail": "Failed to write metadata to account database."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


        # 5. Enqueue Verification Pipeline (FR10, NFR05)
        process_verification_pipeline.delay(file_id, account.account_id)
        
        return Response({
            "file_id": file_id,
            "status": file_upload.status,
            "message": "File accepted. Verification started asynchronously."
        }, status=status.HTTP_202_ACCEPTED)


class FileStatusView(views.APIView):
    permission_classes = [] 

    def get(self, request, file_id, *args, **kwargs):
        user, account = get_user_and_account_data_from_request(request)
        
        if not account:
            return Response({"detail": "Authentication failed."}, status=status.HTTP_401_UNAUTHORIZED)
        
        account_db_name = account.database_name
        
        # 1. Dynamic DB Configuration
        if not configure_account_db(account_db_name):
             return Response({"detail": "Failed to configure account database."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        try:
            # 2. Retrieve FileUpload from the specific account DB
            upload = FileUpload.objects.using(account_db_name).get(file_id=file_id)
            
            # 3. Return response data
            return Response({
                "file_id": upload.file_id,
                "file_name": upload.file_name,
                "status": upload.status,
                "unique_record_count": upload.unique_record_count,
                "filtered_unsubs": upload.filtered_unsub_count,
                "filtered_bounces": upload.filtered_unsub_count, 
                "started_at": upload.started_at,
                "completed_at": upload.completed_at,
            }, status=status.HTTP_200_OK)
            
        except FileUpload.DoesNotExist:
            return Response({"detail": "File not found."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            # Log the error for debugging
            print(f"Error in FileStatusView for {file_id}: {e}")
            return Response({"detail": "Internal server error during status retrieval."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)