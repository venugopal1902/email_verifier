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

# ==============================================================================
# 1. FILE UPLOAD VIEW
# ==============================================================================
class FileUploadView(views.APIView):
    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, format=None):
        serializer = FileListSerializer(data=request.data)
        if serializer.is_valid():
            try:
                # FIX 1: Use 'uploaded_by_user_id' matches your DB
                file_obj = serializer.save(uploaded_by_user_id=request.user.id)
                
                # FIX 2: Access account via User (User -> Account)
                if hasattr(request.user, 'account') and request.user.account:
                    # Just access to ensure it loads
                    _ = request.user.account
                
                process_file_initialization.apply_async(args=[file_obj.id], queue='cpu')
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            except Exception as e:
                logger.error(f"Upload Error: {e}")
                return Response({"error": str(e)}, status=500)
        return Response(serializer.errors, status=400)

# ==============================================================================
# 2. HISTORY VIEW
# ==============================================================================
class FileHistoryView(generics.ListAPIView):
    serializer_class = FileListSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # FIX 3: Filter by 'uploaded_by_user_id'
        return FileUpload.objects.filter(uploaded_by_user_id=self.request.user.id).order_by('-uploaded_at')

class FileListView(FileHistoryView): pass

# ==============================================================================
# 3. SUPPRESSION LISTS
# ==============================================================================
class SuppressionUploadView(views.APIView):
    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, list_type):
        file_obj = request.FILES.get('file')
        if not file_obj: return Response({"error": "No file"}, status=400)
        try:
            model = BouncedEmail if list_type == 'bounced' else UnsubscribedEmail
            df = pd.read_csv(file_obj)
            col = next((c for c in df.columns if 'mail' in c.lower()), df.columns[0])
            emails = df[col].dropna().astype(str).unique().tolist()
            # FIX 4: Use 'uploaded_by_user_id' here too if your model requires it
            objs = [model(email=e, uploaded_by_user_id=str(request.user.id)) for e in emails]
            model.objects.bulk_create(objs, ignore_conflicts=True)
            return Response({"status": "success", "processed_rows": len(emails)}, status=201)
        except Exception as e:
            return Response({"error": str(e)}, status=500)

class SuppressionDeleteView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    def delete(self, request, list_type, email):
        model = BouncedEmail if list_type == 'bounced' else UnsubscribedEmail
        model.objects.filter(email=email).delete()
        return Response({"status": "deleted"}, status=200)

# ==============================================================================
# 4. UTILITIES (Status, Credits, Delete)
# ==============================================================================
class FileStatusView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    def get(self, request, file_id):
        # FIX 5: Filter by correct ID field
        file_obj = get_object_or_404(FileUpload, id=file_id, uploaded_by_user_id=request.user.id)
        return Response({
            "id": file_obj.id, "status": file_obj.status,
            "processed": file_obj.processed_records, "total": file_obj.total_records,
            "valid": file_obj.valid_count, "invalid": file_obj.invalid_count
        })

class CreditBalanceView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    def get(self, request):
        try:
            # FIX 6: Use 'credits_available' and access via request.user.account
            credits = 0
            if hasattr(request.user, 'account') and request.user.account:
                credits = request.user.account.credits_available
            return Response({"credits": credits}, status=200)
        except Exception as e:
            return Response({"credits": 0, "error": str(e)}, status=200)

class FileDeleteView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    def delete(self, request, pk=None):
        # FIX 7: Filter by correct ID field
        FileUpload.objects.filter(id=pk, uploaded_by_user_id=request.user.id).delete()
        return Response({"status": "deleted"}, status=200)

# ==============================================================================
# 5. DOWNLOAD
# ==============================================================================
class DownloadValidCsvView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    def get(self, request, file_id):
        # FIX 8: Filter by correct ID field
        file_obj = get_object_or_404(FileUpload, id=file_id, uploaded_by_user_id=request.user.id)
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="verified_{file_id}.csv"'
        writer = csv.writer(response)
        writer.writerow(['Email Address', 'Status'])
        results = VerificationResult.objects.filter(file=file_obj).iterator()
        for res in results: writer.writerow([res.email, res.final_status])
        return response