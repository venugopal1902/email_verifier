from rest_framework import serializers
from .models import FileUpload

class FileListSerializer(serializers.ModelSerializer):
    class Meta:
        model = FileUpload
        fields = [
            'file_id', 'file_name', 'status', 
            'original_record_count', 
            'unique_record_count',  # Valid
            'invalid_record_count', # <--- ADD THIS FIELD
            'filtered_bounce_count', 
            'filtered_unsub_count', 
            'started_at', 'completed_at'
        ]