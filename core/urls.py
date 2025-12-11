"""
URL configuration for core project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from files.views import FileUploadView, FileStatusView
from accounts.views import LoginView, RegisterView # Ensure views are imported from the correct app

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # --- Authentication Endpoints (PRD Section 4.2) ---
    path('v1/auth/login', LoginView.as_view(), name='auth-login'),
    path('v1/auth/register', RegisterView.as_view(), name='auth-register'),
    
    # --- File/Data Endpoints (PRD Section 4.6) ---
    path('v1/files/upload', FileUploadView.as_view(), name='file-upload'),
    path('v1/files/<str:file_id>', FileStatusView.as_view(), name='file-status'),
    
    # Simple root for UI demonstration
    path('', include('files.urls')), 
]