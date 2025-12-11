from django.db import models
from django.utils import timezone

# --- Per-Account Database Models (Conceptual, as per PRD Section 2.2 B) ---
# NOTE: These models are intended to reside in the dynamically provisioned
# per-account databases, as enforced by the database router.

class BounceEmail(models.Model):
    """BOUNCE_EMAILS - Stores emails identified as hard bounces (FR14, FR15)"""
    email = models.EmailField(unique=True, db_index=True)
    uploaded_by_user_id = models.CharField(max_length=50) # FK to AccountUser.user_id on MAIN DB (stored as ID)
    uploaded_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.email
    
    class Meta:
        db_table = 'bounce_emails'


class FileUpload(models.Model):
    """FILE_UPLOADS - Metadata for uploaded files (FR10)"""
    STATUS_CHOICES = [
        ('UPLOADED', 'Uploaded'), 
        ('PROCESSING', 'Processing'), 
        ('COMPLETED', 'Completed')
    ]

    file_id = models.CharField(max_length=100, primary_key=True)
    file_name = models.CharField(max_length=255)
    file_path = models.FileField(upload_to='uploads/%Y/%m/%d/') # Stores the actual file
    uploaded_by_user_id = models.CharField(max_length=50) # FK to AccountUser.user_id on MAIN DB
    
    original_record_count = models.IntegerField(default=0)
    unique_record_count = models.IntegerField(default=0)
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
    """VERIFICATION_RESULTS - Stores the output of the pipeline (FR20-FR30)"""
    FINAL_STATUS_CHOICES = [
        ('VALID', 'Valid'), 
        ('INVALID', 'Invalid'), 
        ('RISKY', 'Risky'), 
        ('UNKNOWN', 'Unknown')
    ]

    file = models.ForeignKey(FileUpload, on_delete=models.CASCADE, related_name='results')
    email = models.EmailField(db_index=True)
    
    # Verification Stages Results
    syntax_status = models.BooleanField(default=False)
    domain_status = models.BooleanField(default=False)
    smtp_status = models.BooleanField(default=False)
    disposable_status = models.BooleanField(default=False)
    catch_all_status = models.BooleanField(default=False)
    role_based_status = models.BooleanField(default=False)

    final_status = models.CharField(max_length=20, choices=FINAL_STATUS_CHOICES, default='UNKNOWN')
    verified_at = models.DateTimeField(default=timezone.now)

    class Meta:
        # Index on file and email for faster retrieval of results per file
        indexes = [
            models.Index(fields=['file', 'email']),
        ]
        db_table = 'verification_results'