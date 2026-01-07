import uuid
import copy
import pandas as pd
from rest_framework import views, status, serializers
from rest_framework.response import Response
from django.db import connections
from django.contrib.auth import get_user_model
from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from accounts.models import Account
from files.models import FileUpload, BouncedEmail, UnsubscribedEmail
from files.tasks import process_verification_pipeline
from core.redis_utils import add_to_list, delete_from_list
from .serializers import FileListSerializer 

User = get_user_model()

# --- Utility Functions ---
def get_user_and_account_data_from_request(request):
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return None, None
    try:
        token = auth_header.split(' ')[1]
        user_id = int(token.split('_')[1])
        user = User.objects.get(pk=user_id)
        return user, user.account
    except:
        return None, None

def configure_account_db(account_db_name):
    """
    Map the tenant's unique DB alias (e.g. 'db_a1b2') to the MAIN physical database.
    This tricks Django into using the shared 'default' DB for all tenants.
    """
    if account_db_name in connections.databases: return True
    try:
        default_config = settings.DATABASES['default'].copy()
        connections.databases[account_db_name] = default_config
        return True
    except: return False

# --- Views ---

@method_decorator(csrf_exempt, name='dispatch')
class CreditBalanceView(views.APIView):
    authentication_classes = []
    def get(self, request):
        user, account = get_user_and_account_data_from_request(request)
        if not account: return Response(status=401)
        account.refresh_from_db()
        return Response({"credits": account.credits_available})

@method_decorator(csrf_exempt, name='dispatch')
class FileUploadView(views.APIView):
    authentication_classes = [] 
    def post(self, request):
        user, account = get_user_and_account_data_from_request(request)
        if not account: return Response(status=401)
        
        file_obj = request.FILES.get('file')
        if not file_obj: return Response(status=400)
        
        configure_account_db(account.database_name)
        
        # Save File (Tenant Specific Logic remains for Files)
        upload = FileUpload.objects.using(account.database_name).create(
            file_id=str(uuid.uuid4()),
            file_name=file_obj.name,
            file_path=file_obj,
            uploaded_by_user_id=str(user.pk),
            status='UPLOADED'
        )
        
        process_verification_pipeline.delay(upload.file_id, account.account_id)
        
        return Response(FileListSerializer(upload).data, status=202)

@method_decorator(csrf_exempt, name='dispatch')
class FileStatusView(views.APIView):
    authentication_classes = []
    def get(self, request, file_id):
        user, account = get_user_and_account_data_from_request(request)
        if not account: return Response(status=401)
        configure_account_db(account.database_name)
        try:
            upload = FileUpload.objects.using(account.database_name).get(
                file_id=file_id, 
                uploaded_by_user_id=str(user.pk)
            )
            return Response(FileListSerializer(upload).data)
        except: return Response(status=404)

@method_decorator(csrf_exempt, name='dispatch')
class FileListView(views.APIView):
    authentication_classes = []
    def get(self, request):
        user, account = get_user_and_account_data_from_request(request)
        if not account: return Response(status=401)
        
        configure_account_db(account.database_name)
        
        uploads = FileUpload.objects.using(account.database_name).filter(
            uploaded_by_user_id=str(user.pk)
        ).order_by('-started_at')
        
        return Response({"files": FileListSerializer(uploads, many=True).data})

@method_decorator(csrf_exempt, name='dispatch')
class ListUploadView(views.APIView):
    authentication_classes = []
    def post(self, request, list_type):
        user, account = get_user_and_account_data_from_request(request)
        if not account: return Response(status=401)
        
        file_obj = request.FILES.get('file')
        if not file_obj: return Response({"error": "No file"}, status=400)

        if list_type == 'bounce':
            ModelClass = BouncedEmail
        elif list_type == 'unsub':
            ModelClass = UnsubscribedEmail
        else:
            return Response({"error": "Invalid list type"}, status=400)

        try:
            # 1. Smart Column Detection
            header_df = pd.read_csv(file_obj, nrows=0)
            email_col = next((c for c in header_df.columns if 'mail' in c.lower()), header_df.columns[0])
            if hasattr(file_obj, 'seek'): file_obj.seek(0)

            # 2. Process in Chunks
            chunk_size = 5000
            redis_count = 0
            total_processed = 0
            
            for chunk in pd.read_csv(file_obj, chunksize=chunk_size, usecols=[email_col]):
                emails = chunk[email_col].dropna().astype(str).str.lower().str.strip().unique()
                if len(emails) == 0: continue

                # A. Update Redis (GLOBAL SHARED LIST)
                for email in emails:
                    # Pass user_id to track who uploaded it in Redis
                    if add_to_list(email, list_type.upper(), str(user.pk)):
                        redis_count += 1

                # B. Update Database (GLOBAL SHARED DB)
                # Use 'default' database to enforce the "Single Common Database" architecture
                db_objs = [
                    ModelClass(email=email, uploaded_by_user_id=str(user.pk)) 
                    for email in emails
                ]
                ModelClass.objects.using('default').bulk_create(db_objs, ignore_conflicts=True)
                total_processed += len(emails)

            return Response({
                "status": "success",
                "added_to_redis": redis_count,
                "processed_rows": total_processed,
                "message": f"Updated {list_type} list (Global)."
            }, status=200)

        except Exception as e:
            return Response({"error": str(e)}, status=500)

@method_decorator(csrf_exempt, name='dispatch')
class ListDeleteView(views.APIView):
    authentication_classes = []
    def delete(self, request, list_type, email):
        user, account = get_user_and_account_data_from_request(request)
        if not account: return Response(status=401)
        
        # Remove from Global Redis
        delete_from_list(email, list_type.upper())
        
        ModelClass = BouncedEmail if list_type == 'bounce' else UnsubscribedEmail
        
        # Remove from Global DB (Targeting 'default')
        # We only allow users to delete records they uploaded, or admins can delete all.
        # For simplicity in this architecture, we filter by uploaded_by_user_id
        ModelClass.objects.using('default').filter(
            email=email, 
            uploaded_by_user_id=str(user.pk)
        ).delete()

        return Response({"status": "deleted"}, status=200)