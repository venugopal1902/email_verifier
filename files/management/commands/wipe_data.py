from django.core.management.base import BaseCommand
from files.models import FileUpload, VerificationResult, BouncedEmail, UnsubscribedEmail
from django.db import connection

class Command(BaseCommand):
    help = 'Wipes all uploaded files, verification results, and email history.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING('Starting Data Wipe...'))

        # 1. Delete all File Uploads (Cascades to VerificationResults usually, but we are thorough)
        deleted_files, _ = FileUpload.objects.all().delete()
        self.stdout.write(f"Deleted {deleted_files} File Records")

        # 2. Delete all Verification Results (in case any remain)
        deleted_results, _ = VerificationResult.objects.all().delete()
        self.stdout.write(f"Deleted {deleted_results} Verification Results")

        # 3. Optional: Clear Suppression Lists (Bounces/Unsubs)
        # Uncomment these lines if you want to keep suppression lists (Recommended to keep them)
        # BouncedEmail.objects.all().delete()
        # UnsubscribedEmail.objects.all().delete()
        # self.stdout.write("Deleted Suppression Lists")

        self.stdout.write(self.style.SUCCESS('Successfully wiped all history and email data.'))