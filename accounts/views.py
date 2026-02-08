import uuid
import time
from rest_framework import views, status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
# 1. ADD 'login' HERE
from django.contrib.auth import authenticate, login, get_user_model 
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction
from .models import Account
from .serializers import LoginSerializer, RegisterSerializer

User = get_user_model()

@method_decorator(csrf_exempt, name='dispatch')
class LoginView(views.APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = LoginSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        
        email = serializer.validated_data['email']
        password = serializer.validated_data['password']
        
        user = authenticate(request, username=email, password=password)

        if user:
            if not user.is_active:
                return Response({"error": "Account disabled"}, status=403)
            
            # ====================================================
            # 2. THE MISSING PIECE (CRITICAL)
            # This sets the Browser Cookie so the Dashboard works.
            # ====================================================
            login(request, user) 

            return Response({
                "message": "Login Successful",
                "user": {
                    "email": user.email,
                    "role": getattr(user, 'role', 'USER'),
                    "account_id": getattr(user.account, 'account_id', None) if hasattr(user, 'account') else None
                }
            }, status=200)
        
        return Response({"error": "Invalid email or password"}, status=401)

@method_decorator(csrf_exempt, name='dispatch')
class RegisterView(views.APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = RegisterSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        email = serializer.validated_data['email']
        password = serializer.validated_data['password']
        org_name = serializer.validated_data.get('organization_name', 'My Org')

        try:
            with transaction.atomic():
                unique_suffix = uuid.uuid4().hex[:8]
                new_account = Account.objects.create(
                    account_id=f"acct_{unique_suffix}",
                    account_name=org_name,
                    database_name=f"db_{unique_suffix}", 
                    credits_available=1000000
                )

                user = User.objects.create_user(
                    email=email,
                    password=password,
                    role='ADMIN', 
                    account=new_account
                )
                
                # Optional: Log them in immediately after register
                login(request, user) 

            return Response({
                "message": "User registered successfully.",
                "email": user.email
            }, status=201)

        except Exception as e:
            return Response({"error": str(e)}, status=500)