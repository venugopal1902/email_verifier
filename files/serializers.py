from rest_framework import serializers
from .models import FileUpload

class FileListSerializer(serializers.ModelSerializer):
    class Meta:
        model = FileUpload
        fields = [
            'file_id', 
            'file_name', 
            'status', 
            'original_record_count', 
            'unique_record_count',      # Useful / Valid
            'filtered_bounce_count',    # Bounced
            'filtered_unsub_count',     # Unsubscribed
            'started_at', 
            'completed_at'
        ]