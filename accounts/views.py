import uuid
import time
from rest_framework import views, status, serializers
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from django.contrib.auth import authenticate, get_user_model
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction # Import transaction for atomic writes
from .models import Account

User = get_user_model()

# --- Helpers ---
def generate_jwt_and_refresh(user):
    return {
        "access_token": f"access_{user.pk}_{int(time.time())}",
        "user_id": user.pk,
        "email": user.email
    }

# --- Serializers ---
class UserLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

class UserRegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    organization_name = serializers.CharField(required=False, default="My Organization")

# --- Views ---

@method_decorator(csrf_exempt, name='dispatch')
class LoginView(views.APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = UserLoginSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        
        user = authenticate(request, 
                            email=serializer.validated_data['email'], 
                            password=serializer.validated_data['password'])

        if user:
            if not user.is_active:
                return Response({"error": "Account disabled"}, status=403)
            
            tokens = generate_jwt_and_refresh(user)
            acc_id = getattr(user.account, 'account_id', None)

            return Response({
                "access_token": tokens['access_token'],
                "user": {
                    "email": user.email,
                    "role": user.role,
                    "account_id": acc_id
                }
            }, status=200)
        
        return Response({"error": "Invalid email or password"}, status=401)

@method_decorator(csrf_exempt, name='dispatch')
class RegisterView(views.APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = UserRegisterSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        email = serializer.validated_data['email']
        password = serializer.validated_data['password']
        org_name = serializer.validated_data.get('organization_name', 'My Org')

        if User.objects.filter(email=email).exists():
            return Response({"error": "User with this email already exists."}, status=409)

        try:
            with transaction.atomic():
                # 1. Generate Unique ID
                unique_suffix = uuid.uuid4().hex[:8]
                new_account_id = f"acct_{unique_suffix}"
                new_db_name = f"db_{unique_suffix}" 

                # 2. Create Account
                new_account = Account.objects.create(
                    account_id=new_account_id,
                    account_name=org_name,
                    database_name=new_db_name, 
                    credits_available=1000000.00
                )

                # 3. Create User
                user = User.objects.create_user(
                    email=email,
                    password=password,
                    role='ADMIN', 
                    account=new_account
                )

            return Response({
                "message": "User registered successfully.",
                "account_id": new_account_id
            }, status=201)

        except Exception as e:
            print(f"Registration Error: {e}")
            return Response({"error": str(e)}, status=500)