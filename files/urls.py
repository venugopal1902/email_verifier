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
from accounts.views import LoginView, RegisterView
from .views import (
    FileUploadView, FileStatusView, FileListView, 
    ListUploadView, ListDeleteView, CreditBalanceView,
    DownloadValidCsvView # <--- Import this
)
from .views_ui import login_view, register_view, dashboard_view, logout_view

urlpatterns = [
    path('', RedirectView.as_view(url='/login/', permanent=False), name='root'),
    path('login/', login_view, name='login'),
    path('register/', register_view, name='register'),
    path('dashboard/', dashboard_view, name='dashboard'),
    path('logout/', logout_view, name='logout'),

    path('api/v2/auth/login/', LoginView.as_view(), name='auth-login'),
    path('api/v2/auth/register/', RegisterView.as_view(), name='auth-register'),

    path('api/v1/upload/', FileUploadView.as_view(), name='file-upload'),
    path('api/v1/status/<str:file_id>/', FileStatusView.as_view(), name='file-status'),
    path('api/v1/history/', FileListView.as_view(), name='file-history'),
    path('api/v1/credits/', CreditBalanceView.as_view(), name='get-credits'),

    path('api/v1/lists/upload/<str:list_type>/', ListUploadView.as_view(), name='list-upload'),
    path('api/v1/lists/<str:list_type>/<str:email>/', ListDeleteView.as_view(), name='list-delete'),

    # --- DOWNLOAD URL ---
    path('api/v1/download/<str:file_id>/valid/', DownloadValidCsvView.as_view(), name='download-valid'),
]