import logging
import csv
from django.http import HttpResponse, StreamingHttpResponse
from rest_framework import views, status, generics, permissions
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from django.shortcuts import get_object_or_404
from django.db.models import F

# Models & Serializers
from files.models import FileUpload, VerificationResult
from accounts.models import Account  
from .serializers import FileListSerializer

# Tasks
from files.tasks import process_file_initialization

logger = logging.getLogger(__name__)

# ==============================================================================
# 1. FILE UPLOAD VIEW
# ==============================================================================
class FileUploadView(views.APIView):
    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, format=None):
        print(f"\n[WEB DEBUG] >>> Upload Received from: {request.user}")
        
        serializer = FileListSerializer(data=request.data)
        if serializer.is_valid():
            try:
                # 1. Save File
                file_obj = serializer.save(uploaded_by=request.user)
                print(f"[WEB DEBUG] File Saved. ID: {file_obj.id}")
                
                # 2. Check/Deduct Credits (Optional - Safe Check)
                account, _ = Account.objects.get_or_create(user=request.user)
                if account.credits <= 0:
                    print("[WEB WARN] User has 0 credits!")
                    # You can uncomment this to block uploads:
                    # return Response({"error": "Insufficient credits"}, status=402)

                # 3. Dispatch Task
                process_file_initialization.apply_async(
                    args=[file_obj.id], 
                    queue='cpu' 
                )
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            except Exception as e:
                print(f"[WEB CRITICAL] Upload Error: {str(e)}")
                return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

# ==============================================================================
# 2. LIST UPLOAD VIEW (History)
# ==============================================================================
class ListUploadView(generics.ListAPIView):
    serializer_class = FileListSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return FileUpload.objects.filter(uploaded_by=self.request.user).order_by('-uploaded_at')

# ==============================================================================
# 3. FILE LIST VIEW (Alias for compatibility)
# ==============================================================================
class FileListView(ListUploadView):
    """
    Alias for ListUploadView in case the frontend calls this specific class name.
    """
    pass

# ==============================================================================
# 4. FILE STATUS VIEW (Polling)
# ==============================================================================
class FileStatusView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, file_id):
        try:
            file_obj = FileUpload.objects.get(id=file_id, uploaded_by=request.user)
            return Response({
                "id": file_obj.id,
                "status": file_obj.status,
                "processed": file_obj.processed_records,
                "total": file_obj.total_records,
                "valid": file_obj.valid_count,
                "invalid": file_obj.invalid_count
            }, status=status.HTTP_200_OK)
        except FileUpload.DoesNotExist:
            return Response({"error": "File not found"}, status=status.HTTP_404_NOT_FOUND)

# ==============================================================================
# 5. LIST DELETE VIEW
# ==============================================================================
class ListDeleteView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, pk=None, file_id=None):
        # Allow looking up by 'pk' (standard) or 'file_id' (legacy)
        id_to_delete = pk if pk else file_id
        
        try:
            file_obj = FileUpload.objects.get(id=id_to_delete, uploaded_by=request.user)
            file_obj.delete() 
            return Response({"status": "deleted"}, status=status.HTTP_200_OK)
        except FileUpload.DoesNotExist:
            return Response({"error": "File not found"}, status=status.HTTP_404_NOT_FOUND)

# ==============================================================================
# 6. CREDIT BALANCE VIEW (Fixes 'Credits not showing')
# ==============================================================================
class CreditBalanceView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        try:
            # get_or_create prevents crash if Account is missing
            account, created = Account.objects.get_or_create(
                user=request.user,
                defaults={'credits': 0}
            )
            return Response({"credits": account.credits}, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"Credit Fetch Error: {e}")
            return Response({"credits": 0, "error": str(e)}, status=status.HTTP_200_OK)

# ==============================================================================
# 7. DOWNLOAD VALID CSV VIEW
# ==============================================================================
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