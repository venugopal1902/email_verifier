import uuid
from rest_framework import serializers
from .models import FileUpload

class FileListSerializer(serializers.ModelSerializer):
    # Map frontend 'file' to backend 'file_path'
    file = serializers.FileField(source='file_path', write_only=True)

    class Meta:
        model = FileUpload
        fields = '__all__'
        read_only_fields = [
            'file_id', 
            'file_name', 
            'file_path', 
            'uploaded_by', 
            'uploaded_by_user_id', 
            'status', 
            'uploaded_at', 
            'started_at', 
            'completed_at',
            'processed_records',
            'total_records',
            'valid_count',
            'invalid_count',
            'filtered_bounce_count',
            'filtered_unsub_count'
        ]

    def create(self, validated_data):
        # 1. GENERATE UNIQUE ID (Critical Fix)
        validated_data['file_id'] = str(uuid.uuid4())

        # 2. Extract Filename
        uploaded_file = validated_data.get('file_path')
        if uploaded_file:
            validated_data['file_name'] = uploaded_file.name
            
        return super().create(validated_data)