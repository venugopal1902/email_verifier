import logging
import csv
import pandas as pd
from django.http import HttpResponse
from rest_framework import views, status, generics, permissions
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from django.shortcuts import get_object_or_404

# Models
from files.models import FileUpload, VerificationResult, BouncedEmail, UnsubscribedEmail
from accounts.models import Account  
from .serializers import FileListSerializer
from files.tasks import process_file_initialization

# Redis Utility
from core.redis_utils import add_to_list

logger = logging.getLogger(__name__)

# ==========================================
# 1. FILE UPLOAD VIEW
# ==========================================
class FileUploadView(views.APIView):
    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, format=None):
        print(f"[DEBUG] Upload Request Data: {request.data}")
        
        serializer = FileListSerializer(data=request.data)
        if serializer.is_valid():
            try:
                file_obj = serializer.save(uploaded_by_user_id=str(request.user.id))
                
                if hasattr(request.user, 'account') and request.user.account:
                    _ = request.user.account
                
                process_file_initialization.apply_async(args=[file_obj.file_id], queue='cpu')
                
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            except Exception as e:
                logger.error(f"Upload System Error: {e}")
                return Response({"error": f"Upload System Error: {str(e)}"}, status=500)
        else:
            print(f"[DEBUG] Validation Errors: {serializer.errors}")
            return Response(serializer.errors, status=400)

# ==========================================
# 2. HISTORY VIEW
# ==========================================
class FileHistoryView(generics.ListAPIView):
    serializer_class = FileListSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return FileUpload.objects.filter(
            uploaded_by_user_id=str(self.request.user.id)
        ).order_by('-uploaded_at')

class FileListView(FileHistoryView): pass

# ==========================================
# 3. SUPPRESSION LISTS
# ==========================================
class SuppressionUploadView(views.APIView):
    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [permissions.IsAuthenticated]
    def post(self, request, list_type):
        file_obj = request.FILES.get('file')
        if not file_obj: return Response({"error": "No file"}, status=400)
        try:
            if list_type == 'bounced':
                model = BouncedEmail
                redis_list_type = 'BOUNCE'
            else:
                model = UnsubscribedEmail
                redis_list_type = 'UNSUB'
                
            # Read CSV
            df = pd.read_csv(file_obj)
            col = next((c for c in df.columns if 'mail' in c.lower()), df.columns[0])
            emails = df[col].dropna().astype(str).str.lower().str.strip().unique().tolist()
            
            user_id_str = str(request.user.id)

            # Save to Postgres DB
            objs = [model(email=e, uploaded_by_user_id=user_id_str) for e in emails]
            model.objects.bulk_create(objs, ignore_conflicts=True)

            # Save to Redis Shards using your utility
            for email in emails:
                add_to_list(email=email, list_type=redis_list_type, user_id=user_id_str)

            return Response({
                "status": "success", 
                "processed_rows": len(emails),
                "redis_updated": True
            }, status=201)
        except Exception as e:
            return Response({"error": str(e)}, status=500)

class SuppressionDeleteView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    def delete(self, request, list_type, email):
        model = BouncedEmail if list_type == 'bounced' else UnsubscribedEmail
        model.objects.filter(email=email).delete()
        return Response({"status": "deleted"}, status=200)

# ==========================================
# 4. UTILITIES
# ==========================================
class FileStatusView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request, file_id):
        file_obj = get_object_or_404(FileUpload, file_id=file_id, uploaded_by_user_id=str(request.user.id))
        print(f"[DEBUG] Status Check for File ID: {file_id} - Status: {file_obj.status}, Processed: {file_obj.processed_records}/{file_obj.total_records}, bounce: {file_obj.filtered_bounce_count}, unsub: {file_obj.filtered_unsub_count}")
        return Response({
            "id": file_obj.file_id, 
            "status": file_obj.status,
            "processed": file_obj.processed_records, 
            "total": file_obj.total_records,
            # FIX: Map to the correct database columns used in tasks.py
            "valid": file_obj.unique_record_count,       
            "invalid": file_obj.invalid_record_count,
            "bounced": file_obj.filtered_bounce_count,   
            "unsub": file_obj.filtered_unsub_count       
        })

class CreditBalanceView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    def get(self, request):
        try:
            credits = 0
            if hasattr(request.user, 'account') and request.user.account:
                credits = request.user.account.credits_available
            return Response({"credits": credits}, status=200)
        except Exception as e:
            return Response({"credits": 0, "error": str(e)}, status=200)

class FileDeleteView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    def delete(self, request, pk=None):
        FileUpload.objects.filter(file_id=pk, uploaded_by_user_id=str(request.user.id)).delete()
        return Response({"status": "deleted"}, status=200)

# ==========================================
# 5. DOWNLOAD
# ==========================================
class DownloadValidCsvView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    def get(self, request, file_id):
        file_obj = get_object_or_404(FileUpload, file_id=file_id, uploaded_by_user_id=str(request.user.id))
        
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="verified_{file_id}.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['Email Address', 'Status'])
        results = VerificationResult.objects.filter(file=file_obj).iterator()
        for res in results: writer.writerow([res.email, res.final_status])
        return response