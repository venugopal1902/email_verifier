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

logger = logging.getLogger(__name__)

# ==========================================
# 1. FILE UPLOAD & PROCESSING
# ==========================================
class FileUploadView(views.APIView):
    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, format=None):
        serializer = FileListSerializer(data=request.data)
        if serializer.is_valid():
            try:
                # Save File
                file_obj = serializer.save(uploaded_by=request.user)
                # Ensure Account Exists
                Account.objects.get_or_create(user=request.user, defaults={'credits': 0})
                # Trigger Task
                process_file_initialization.apply_async(args=[file_obj.id], queue='cpu')
                
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            except Exception as e:
                return Response({"error": str(e)}, status=500)
        return Response(serializer.errors, status=400)

# ==========================================
# 2. DASHBOARD HISTORY
# ==========================================
class FileHistoryView(generics.ListAPIView):
    serializer_class = FileListSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return FileUpload.objects.filter(uploaded_by=self.request.user).order_by('-uploaded_at')

# ==========================================
# 3. SUPPRESSION LISTS (Bounced/Unsub)
# ==========================================
class SuppressionUploadView(views.APIView):
    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, list_type):
        file_obj = request.FILES.get('file')
        if not file_obj:
            return Response({"error": "No file provided"}, status=400)

        try:
            # Determine Model
            model = BouncedEmail if list_type == 'bounced' else UnsubscribedEmail
            
            # Read CSV
            df = pd.read_csv(file_obj)
            # Find email column (simple search)
            col = next((c for c in df.columns if 'mail' in c.lower()), df.columns[0])
            emails = df[col].dropna().astype(str).unique().tolist()

            # Bulk Create (Ignore duplicates)
            objs = [
                model(email=email, uploaded_by_user_id=str(request.user.id)) 
                for email in emails
            ]
            model.objects.bulk_create(objs, ignore_conflicts=True)

            return Response({"status": "success", "processed_rows": len(emails)}, status=201)
        except Exception as e:
            logger.error(f"Suppression Upload Error: {e}")
            return Response({"error": str(e)}, status=500)

class SuppressionDeleteView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, list_type, email):
        model = BouncedEmail if list_type == 'bounced' else UnsubscribedEmail
        model.objects.filter(email=email).delete()
        return Response({"status": "deleted"}, status=200)

# ==========================================
# 4. UTILITIES (Status, Credits, Delete)
# ==========================================
class FileStatusView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    def get(self, request, file_id):
        file_obj = get_object_or_404(FileUpload, id=file_id, uploaded_by=request.user)
        return Response({
            "id": file_obj.id,
            "status": file_obj.status,
            "processed": file_obj.processed_records,
            "total": file_obj.total_records,
            "valid": file_obj.valid_count,
            "invalid": file_obj.invalid_count
        })

class CreditBalanceView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    def get(self, request):
        acct, _ = Account.objects.get_or_create(user=request.user, defaults={'credits': 0})
        return Response({"credits": acct.credits})

class FileDeleteView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    def delete(self, request, pk=None):
        file_obj = get_object_or_404(FileUpload, id=pk, uploaded_by=request.user)
        file_obj.delete()
        return Response({"status": "deleted"})

# ==========================================
# 5. DOWNLOAD
# ==========================================
class DownloadValidCsvView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request, file_id):
        file_obj = get_object_or_404(FileUpload, id=file_id, uploaded_by=request.user)
        
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="verified_{file_id}.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['Email Address', 'Status']) 
        
        results = VerificationResult.objects.filter(file=file_obj).iterator()
        for res in results:
            writer.writerow([res.email, res.final_status])
            
        return response