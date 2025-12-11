from rest_framework import views, status, serializers
from rest_framework.response import Response
from django.contrib.auth import authenticate, get_user_model
from django.db import IntegrityError
from accounts.models import Account # Import Account model for linking
import time 

User = get_user_model()

# --- Utility Functions (for simulation) ---

def generate_jwt_and_refresh(user):
    """
    Simulates generating JWT tokens (FR39).
    """
    # Simple placeholder logic
    access_token = f"access_{user.pk}_{time.time()}"
    refresh_token = f"refresh_{user.pk}_{time.time()}"
    return {
        "access_token": access_token,
        "expires_in": 3600,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
    }

class UserLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

class LoginView(views.APIView):
    """
    Handles POST /v1/auth/login
    Authenticates user and returns JWT tokens (FR39, FR40).
    """
    # Disable session-based authentication for this endpoint so CSRF is not required
    authentication_classes = []
    permission_classes = [] 

    def post(self, request):
        serializer = UserLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        email = serializer.validated_data['email']
        password = serializer.validated_data['password']

        # Use Django's built-in authentication system
        user = authenticate(request, email=email, password=password)

        if user is not None:
            if user.is_active:
                tokens = generate_jwt_and_refresh(user)
                
                # Retrieve the database_name for context (although not sent in the API response, it's used internally)
                account_id = user.account.account_id if user.account else None

                return Response({
                    **tokens,
                    "user": {
                        "user_id": user.pk,
                        "email": user.email,
                        "role": user.role,
                        "account_id": account_id
                    }
                }, status=status.HTTP_200_OK)
            else:
                return Response({"error": "User account is inactive."}, status=status.HTTP_403_FORBIDDEN)
        else:
            return Response({"error": "Invalid email or password."}, status=status.HTTP_401_UNAUTHORIZED)


class UserRegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    role = serializers.CharField(required=False, default='USER')
    account_id = serializers.CharField(required=False, allow_null=True)

    def validate_account_id(self, value):
        if value:
            try:
                self.account = Account.objects.get(account_id=value)
            except Account.DoesNotExist:
                raise serializers.ValidationError("Account ID does not exist.")
        return value

class RegisterView(views.APIView):
    """
    Endpoint for user creation (FR01, FR04). Used by Admin to provision users/owners.
    """
    # Disable session-based authentication for registration endpoint as well
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        serializer = UserRegisterSerializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
            
            account_instance = serializer.account if hasattr(serializer, 'account') else None
            
            # Create user in the Main DB
            user = User.objects.create_user(
                email=serializer.validated_data['email'],
                password=serializer.validated_data['password'],
                role=serializer.validated_data.get('role', 'USER'),
                account=account_instance
            )
            
            return Response({
                "user_id": user.pk, 
                "email": user.email, 
                "role": user.role
            }, status=status.HTTP_201_CREATED)

        except IntegrityError:
            return Response({"error": "A user with this email already exists."}, status=status.HTTP_409_CONFLICT)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)