from django.urls import path
from django.views.generic import TemplateView

urlpatterns = [
    # Simple UI view for testing the upload functionality
    path('', TemplateView.as_view(template_name='dashboard.html'), name='dashboard'),
]