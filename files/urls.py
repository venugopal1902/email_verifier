# from django.urls import path
# from django.views.generic import TemplateView
# from django.shortcuts import redirect

# def dashboard_view(request):
#     if not request.user.is_authenticated:
#         return redirect('/login')
#     return TemplateView.as_view(template_name='dashboard.html')(request)

# urlpatterns = [
#     path('login', TemplateView.as_view(template_name='login.html'), name='login'),
#     path('register', TemplateView.as_view(template_name='register.html'), name='register'),
#     path('dashboard', dashboard_view, name='dashboard'),
#     path('', dashboard_view, name='root-dashboard'),
# ]
from django.urls import path
from django.views.generic import RedirectView

# 1. UI Views
from .views_ui import (
    login_view, 
    register_view, 
    dashboard_view, 
    logout_view
)

# 2. API Views (Auth)
from accounts.views import LoginView, RegisterView

# 3. API Views (Files)
from .views import (
    FileUploadView, 
    FileHistoryView, 
    FileStatusView, 
    FileDeleteView, 
    CreditBalanceView, 
    DownloadValidCsvView,
    SuppressionUploadView, 
    SuppressionDeleteView
)

urlpatterns = [
    # ==========================================
    # 1. UI ROUTES
    # ==========================================
    path('', RedirectView.as_view(url='/login/', permanent=False), name='root'),
    path('login/', login_view, name='login'),
    path('register/', register_view, name='register'),
    path('dashboard/', dashboard_view, name='dashboard'),
    path('logout/', logout_view, name='logout'),

    # ==========================================
    # 2. API AUTH ROUTES
    # ==========================================
    path('api/v2/auth/login/', LoginView.as_view(), name='auth-login'),
    path('api/v2/auth/register/', RegisterView.as_view(), name='auth-register'),

    # ==========================================
    # 3. API FILE ROUTES (v1)
    # ==========================================
    path('api/v1/upload/', FileUploadView.as_view(), name='file-upload'),
    path('api/v1/history/', FileHistoryView.as_view(), name='file-history'),
    
    # FIX: Change <int:...> to <str:...> for UUIDs
    path('api/v1/status/<str:file_id>/', FileStatusView.as_view(), name='file-status'),
    path('api/v1/delete/<str:pk>/', FileDeleteView.as_view(), name='file-delete'),
    
    path('api/v1/credits/', CreditBalanceView.as_view(), name='credits'),
    
    # FIX: Change <int:file_id> to <str:file_id>
    path('api/v1/download/<str:file_id>/', DownloadValidCsvView.as_view(), name='file-download'),
    path('api/v1/download/<str:file_id>/valid/', DownloadValidCsvView.as_view(), name='file-download-valid'),

    path('api/v1/lists/upload/<str:list_type>/', SuppressionUploadView.as_view(), name='list-upload'),
    path('api/v1/lists/<str:list_type>/<str:email>/', SuppressionDeleteView.as_view(), name='list-delete'),
]