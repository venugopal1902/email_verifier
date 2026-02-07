import logging
import csv
from django.http import StreamingHttpResponse, HttpResponse
from rest_framework import views, status, generics, permissions
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from django.shortcuts import get_object_or_404
from django.db.models import F

# Models & Serializers
from files.models import FileUpload, VerificationResult
from accounts.models import Account  # Assuming Account holds credits
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
        print(f"\n[WEB DEBUG] >>> Upload Received from user: {request.user}")
        
        serializer = FileListSerializer(data=request.data)
        
        if serializer.is_valid():
            try:
                # 1. Save to DB
                file_obj = serializer.save(uploaded_by=request.user)
                print(f"[WEB DEBUG] File Saved. ID: {file_obj.id}")
                
                # 2. Dispatch Task (CPU Queue)
                print(f"[WEB DEBUG] Sending task to CPU queue...")
                process_file_initialization.apply_async(
                    args=[file_obj.id], 
                    queue='cpu' 
                )
                
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            except Exception as e:
                print(f"[WEB CRITICAL] Error: {str(e)}")
                return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

# ==============================================================================
# 2. LIST VIEWS (Providing both names to prevent ImportErrors)
# ==============================================================================
class ListUploadView(generics.ListAPIView):
    serializer_class = FileListSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return FileUpload.objects.filter(uploaded_by=self.request.user).order_by('-uploaded_at')

# Alias for backward compatibility if urls.py uses 'FileListView'
class FileListView(ListUploadView):
    pass

# ==============================================================================
# 3. FILE STATUS VIEW (Polling Endpoint)
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
# 4. DELETE VIEW
# ==============================================================================
class ListDeleteView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, pk):
        # Note: 'pk' is standard for generic views, 'file_id' for custom. 
        # We try both to be safe.
        id_to_delete = pk if pk else request.GET.get('id')
        
        try:
            file_obj = FileUpload.objects.get(id=id_to_delete, uploaded_by=request.user)
            file_obj.delete() 
            return Response({"status": "deleted"}, status=status.HTTP_200_OK)
        except FileUpload.DoesNotExist:
            return Response({"error": "File not found"}, status=status.HTTP_404_NOT_FOUND)

# ==============================================================================
# 5. CREDIT BALANCE VIEW
# ==============================================================================
class CreditBalanceView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        try:
            # Attempt to find Account associated with user
            account = Account.objects.filter(user=request.user).first()
            credits = account.credits if account else 0
            
            return Response({"credits": credits}, status=status.HTTP_200_OK)
        except Exception as e:
            # Fallback if no Account model exists yet
            return Response({"credits": 0, "error": str(e)}, status=status.HTTP_200_OK)

# ==============================================================================
# 6. DOWNLOAD CSV VIEW
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