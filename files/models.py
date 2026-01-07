from django.db import models
from django.utils import timezone

# --- Global Shared Models ---

class BouncedEmail(models.Model):
    email = models.EmailField(unique=True, db_index=True)
    uploaded_by_user_id = models.CharField(max_length=50, null=True, blank=True) 
    uploaded_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.email
    
    class Meta:
        db_table = 'bounce_emails'


class UnsubscribedEmail(models.Model):
    email = models.EmailField(unique=True, db_index=True)
    uploaded_by_user_id = models.CharField(max_length=50, null=True, blank=True)
    uploaded_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.email

    class Meta:
        db_table = 'unsubscribed_emails'        


class FileUpload(models.Model):
    """FILE_UPLOADS - Metadata for uploaded files"""
    STATUS_CHOICES = [
        ('UPLOADED', 'Uploaded'), 
        ('PROCESSING', 'Processing'), 
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed')
    ]

    file_id = models.CharField(max_length=100, primary_key=True)
    file_name = models.CharField(max_length=255)
    file_path = models.FileField(upload_to='uploads/%Y/%m/%d/') 
    uploaded_by_user_id = models.CharField(max_length=50) 
    
    original_record_count = models.IntegerField(default=0)
    unique_record_count = models.IntegerField(default=0)   # Valid Count
    invalid_record_count = models.IntegerField(default=0)  # <--- NEW FIELD
    filtered_unsub_count = models.IntegerField(default=0)
    filtered_bounce_count = models.IntegerField(default=0)
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='UPLOADED')
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    def __str__(self):
        return self.file_name
    
    class Meta:
        db_table = 'file_uploads'


class VerificationResult(models.Model):
    """VERIFICATION_RESULTS - Stores the output of the pipeline"""
    FINAL_STATUS_CHOICES = [
        ('VALID', 'Valid'), 
        ('INVALID', 'Invalid'), 
        ('RISKY', 'Risky'), 
        ('UNKNOWN', 'Unknown')
    ]

    file = models.ForeignKey(FileUpload, on_delete=models.CASCADE, related_name='results')
    email = models.EmailField(db_index=True)
    
    # --- Verification Stages Results ---
    syntax_status = models.BooleanField(default=False)
    domain_status = models.BooleanField(default=False)
    smtp_status = models.BooleanField(default=False)
    
    # Advanced Checks
    greylisted = models.BooleanField(default=False)
    smart_verify_status = models.BooleanField(default=False)
    
    disposable_status = models.BooleanField(default=False)
    catch_all_status = models.BooleanField(default=False)
    free_mail_status = models.BooleanField(default=False)
    role_based_status = models.BooleanField(default=False)
    
    final_status = models.CharField(max_length=20, choices=FINAL_STATUS_CHOICES, default='UNKNOWN')
    verified_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=['file', 'email']),
        ]
        db_table = 'verification_results'