import time
import uuid
import random
import csv
import pandas as pd
from celery import shared_task
from django.db import connection, connections
from django.conf import settings

# Import models using their full path to avoid circular imports in the app structure
from accounts.models import Account
from files.models import FileUpload, VerificationResult
from core.db_routers import MAIN_DB_LABEL

@shared_task(bind=True)
def process_verification_pipeline(self, file_id, account_id):
    """
    Main task to handle the entire CSV processing and email verification.
    (NFR05: Parallel processing, FR13: Filters, FR20-FR30: Verification Pipeline)
    """
    try:
        # 1. Get Account (from MAIN_DB) to get the specific account database name
        main_db = connections[MAIN_DB_LABEL]
        with main_db.cursor() as cursor:
            # We must manually query the database_name as we are outside Django ORM context 
            # for the subsequent connections unless we configure them manually.
            cursor.execute(f"SELECT database_name, credits_available FROM accounts_account WHERE account_id = %s", [account_id])
            account_data = cursor.fetchone()
            if not account_data:
                print(f"Error: Account {account_id} not found in main database.")
                return

        account_db_name = account_data[0]
        initial_credits = account_data[1]

        # 2. Configure the dynamic database connection for the task
        if account_db_name not in settings.DATABASES:
            # NOTE: In a real system, a provisioning service would ensure this DB exists.
            # We add it dynamically for the scope of the task.
            connections.databases[account_db_name] = {
                'ENGINE': 'django.db.backends.postgresql',
                'NAME': account_db_name,
                'USER': os.getenv('SQL_USER'),
                'PASSWORD': os.getenv('SQL_PASSWORD'),
                'HOST': os.getenv('SQL_HOST'),
                'PORT': os.getenv('SQL_PORT'),
            }
        
        # 3. Retrieve FileUpload model instance from the account DB
        upload = FileUpload.objects.using(account_db_name).get(file_id=file_id)
        upload.status = 'PROCESSING'
        upload.started_at = timezone.now()
        upload.save(using=account_db_name)

        # 4. Load & Clean Data (FR11: Deduplicate, FR13: Filter)
        df = pd.read_csv(upload.file_path.path)
        emails = df.iloc[:, 0].dropna().unique().tolist() # Assume first column is emails

        total_records = len(emails)
        unique_records = len(emails)
        
        # Simulate filtering with Bounce/Unsubscribe Lists (FR13, NFR05)
        # In a high-performance system, these lists would be in Redis (O(1) lookup).
        # We simulate the lookup speed here.
        filtered_emails = []
        filtered_bounces = 0
        filtered_unsubs = 0

        # Simulate Redis/DB lookups for filtering
        for email in emails:
            # Simulate bounce/unsub check
            if random.random() < 0.05: # 5% chance of being a bounce
                filtered_bounces += 1
                continue
            if random.random() < 0.03: # 3% chance of being unsubscribed
                filtered_unsubs += 1
                continue
            filtered_emails.append(email)

        emails_to_verify = filtered_emails
        emails_count = len(emails_to_verify)
        
        # 5. Check Credits (FR32)
        if initial_credits < emails_count:
            # Revert status, notify user (FR32, FR38)
            upload.status = 'COMPLETED'
            upload.save(using=account_db_name)
            print(f"Task aborted for {account_id}: Insufficient credits.")
            return

        # 6. Deduct Credits (FR31, FR34)
        # IMPORTANT: This must be a transactional, concurrent-safe update in the main DB.
        # Use F-expressions or database locks in a real scenario.
        # We simulate the deduction here.
        new_credits = initial_credits - emails_count
        with main_db.cursor() as cursor:
            cursor.execute(f"UPDATE accounts_account SET credits_available = %s WHERE account_id = %s", [new_credits, account_id])

        
        # 7. Start Verification Pipeline (FR20-FR30)
        verification_results = []
        final_valid_count = 0
        
        for i, email in enumerate(emails_to_verify):
            # Simulate the 10-step verification pipeline (FR20-FR30)
            time.sleep(0.001) # Simulate network/API latency (adjust for NFR01)
            
            # Simple simulation logic
            is_valid = random.choice([True, False])
            final_status = 'VALID' if is_valid else random.choice(['INVALID', 'RISKY'])
            if final_status == 'VALID':
                final_valid_count += 1
            
            verification_results.append(VerificationResult(
                file=upload,
                email=email,
                syntax_status=True, # Assume passed for valid emails
                domain_status=is_valid,
                smtp_status=is_valid,
                final_status=final_status
            ))
            
            self.update_state(state='PROGRESS', meta={'current': i + 1, 'total': emails_count})

        # 8. Bulk Save Results to Account DB
        VerificationResult.objects.using(account_db_name).bulk_create(verification_results)
        
        # 9. Finalize Upload Status and Counts (FR36)
        upload.unique_record_count = unique_records
        upload.filtered_bounce_count = filtered_bounces
        upload.filtered_unsub_count = filtered_unsubs
        upload.status = 'COMPLETED'
        upload.completed_at = timezone.now()
        upload.save(using=account_db_name)

        # 10. Trigger Archiving (FR35) - Run immediately or enqueue another task
        # We will enqueue it as a separate task
        archive_file_results.delay(upload.file_id, account_id)
        
        print(f"Verification completed for file {file_id}. Valid: {final_valid_count}")

    except FileUpload.DoesNotExist:
        print(f"File {file_id} not found in database {account_db_name}.")
    except Exception as e:
        print(f"An error occurred during verification for file {file_id}: {e}")
        # Log error, potentially update upload status to 'FAILED'
    finally:
        # Important: Close the dynamic connection after the task finishes
        if account_db_name in connections.databases:
            connections[account_db_name].close()


@shared_task
def archive_file_results(file_id, account_id):
    """
    Task to move completed results to a permanent archive (FR35, FR36).
    """
    # NOTE: In a real system, this would export to S3/GCS and then delete from the active DB.
    # The database connection handling (step 1 and 2 from above) would be repeated here.
    print(f"Archiving task started for file {file_id} in account {account_id}...")
    time.sleep(5) # Simulate archiving time
    print(f"Archiving completed and active data cleanup finished for file {file_id}.")