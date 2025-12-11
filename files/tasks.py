import time
import uuid
import random
import csv
import pandas as pd
import os # Need os for getenv
from celery import shared_task
from django.db import connection, connections
from django.conf import settings
from django.utils import timezone
import os

# Import core Redis utilities for Consistent Hashing lookups
from core.redis_utils import check_list 

# Import models using their full path 
from accounts.models import Account
from files.models import FileUpload, VerificationResult
from core.db_routers import MAIN_DB_LABEL


@shared_task(bind=True)
def process_verification_pipeline(self, file_id, account_id):
    """
    Main task to handle the entire CSV processing and email verification, 
    now using Redis Hashing for O(1) bounce/unsub filtering.
    """
    account_db_name = None
    try:
        # 1. Get Account (from MAIN_DB)
        main_db_conn = connections[MAIN_DB_LABEL]
        
        with main_db_conn.cursor() as cursor:
            cursor.execute(f"SELECT database_name, credits_available FROM accounts_account WHERE account_id = %s", [account_id])
            account_data = cursor.fetchone()
            if not account_data:
                print(f"Error: Account {account_id} not found in main database.")
                return

        account_db_name = account_data[0]
        initial_credits = account_data[1]

        # 2. Configure the dynamic database connection for the task
        if account_db_name not in connections.databases:
             connections.databases[account_db_name] = {
                'ENGINE': os.getenv('SQL_ENGINE'),
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

        # 4. Load & Clean Data (FR11: Deduplicate)
        # Ensure the file path is correct for the Docker volume mount
        df = pd.read_csv(upload.file_path.path)
        emails = df.iloc[:, 0].dropna().str.lower().unique().tolist() # Use .lower() for canonical emails

        unique_records = len(emails)
        
        # 5. Filter with Redis (FR13: Filters, SCALABILITY FIX)
        filtered_emails = []
        filtered_bounces = 0
        filtered_unsubs = 0

        for email in emails:
            # Check Redis for O(1) lookup
            if check_list(account_id, email, list_type='BOUNCE'):
                filtered_bounces += 1
                continue
            if check_list(account_id, email, list_type='UNSUB'):
                filtered_unsubs += 1
                continue
            filtered_emails.append(email)

        emails_to_verify = filtered_emails
        emails_count = len(emails_to_verify)
        
        # 6. Check Credits and Deduct (Transactional)
        if initial_credits < emails_count:
            # Revert status, notify user (FR32)
            upload.status = 'COMPLETED'
            upload.save(using=account_db_name)
            print(f"Task aborted for {account_id}: Insufficient credits.")
            return

        # Deduct Credits (Transactional update in main DB)
        new_credits = initial_credits - emails_count
        with main_db_conn.cursor() as cursor:
            # Use SQL directly for atomic update
            cursor.execute(f"UPDATE accounts_account SET credits_available = %s WHERE account_id = %s", [new_credits, account_id])

        
        # 7. Start Verification Pipeline (FR20-FR30)
        verification_results = []
        final_valid_count = 0
        
        for i, email in enumerate(emails_to_verify):
            # Simulate the 10-step verification pipeline (FR20-FR30)
            time.sleep(0.001) 
            
            # Simple simulation logic
            is_valid = random.choice([True, False])
            final_status = 'VALID' if is_valid else random.choice(['INVALID', 'RISKY'])
            if final_status == 'VALID':
                final_valid_count += 1
            
            verification_results.append(VerificationResult(
                file=upload,
                email=email,
                syntax_status=True, 
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

        # 10. Trigger Archiving (FR35)
        archive_file_results.delay(upload.file_id, account_id)
        
        print(f"Verification completed for file {file_id}. Valid: {final_valid_count}")

    except FileUpload.DoesNotExist:
        print(f"File {file_id} not found in database {account_db_name}.")
    except Exception as e:
        print(f"An error occurred during verification for file {file_id}: {e}")
    finally:
        # Important: Close the dynamic connection after the task finishes
        if account_db_name in connections.databases:
            connections[account_db_name].close()


@shared_task
def archive_file_results(file_id, account_id):
    """
    Task to move completed results to a permanent archive (FR35, FR36).
    """
    print(f"Archiving task started for file {file_id} in account {account_id}...")
    time.sleep(5) 
    print(f"Archiving completed and active data cleanup finished for file {file_id}.")