from rest_framework import serializers
from django.contrib.auth import get_user_model

User = get_user_model()

class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    organization_name = serializers.CharField(required=False, default="My Organization")

    def validate_email(self, value):
        """
        Check if the email already exists in the database.
        """
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("User with this email already exists.")
        return value

    def create(self, validated_data):
        """
        This method is called when serializer.save() is run.
        It creates a new user using the custom manager.
        """
        # Note: The actual user creation logic with Account generation 
        # is currently handled in the View for atomic transactions.
        # However, for cleaner code, you can move logic here later.
        # For now, this is just a validator shell used by your View.
        return validated_data