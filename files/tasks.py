import time
import uuid
import random
import pandas as pd
import os
from celery import shared_task
from django.db import connection, connections
from django.conf import settings
from django.utils import timezone

# Import core Redis utilities
from core.redis_utils import check_list 
from accounts.models import Account
from files.models import FileUpload, VerificationResult
from core.db_routers import MAIN_DB_LABEL

@shared_task(bind=True)
def process_verification_pipeline(self, file_id, account_id):
    """
    Main verification task.
    """
    print(f"--- [TASK STARTED] File: {file_id}, Account: {account_id} ---")
    account_db_name = None
    
    def ensure_account_db_configured(db_name):
        """
        Map tenant DB alias to the physical default DB.
        """
        if db_name not in connections.databases:
            if 'default' in settings.DATABASES:
                new_config = settings.DATABASES['default'].copy()
                connections.databases[db_name] = new_config
                print(f"--- [DB CONFIG] Configured alias '{db_name}' to point to default DB. ---")
            else:
                print("--- [DB ERROR] 'default' database config missing in settings! ---")

    try:
        # 1. Connect to Main DB to get Tenant Info
        try:
            main_db_conn = connections[MAIN_DB_LABEL]
            with main_db_conn.cursor() as cursor:
                cursor.execute("SELECT database_name, credits_available FROM accounts_account WHERE account_id = %s", [account_id])
                account_data = cursor.fetchone()
                if not account_data: 
                    print(f"--- [ERROR] Account {account_id} not found in Main DB. ---")
                    return
        except Exception as e:
            print(f"--- [DB CONNECTION ERROR] Could not connect to Main DB: {e} ---")
            return

        account_db_name = account_data[0]
        initial_credits = account_data[1]
        
        # 2. Configure the Tenant Alias
        ensure_account_db_configured(account_db_name)
        
        # 3. Retrieve the Upload Object
        try:
            upload = FileUpload.objects.using(account_db_name).get(file_id=file_id)
            upload.status = 'PROCESSING'
            upload.started_at = timezone.now()
            upload.save(using=account_db_name)
            print(f"--- [STATUS UPDATE] File {file_id} status set to PROCESSING. ---")
        except FileUpload.DoesNotExist:
            print(f"--- [ERROR] FileUpload {file_id} not found in DB '{account_db_name}'. ---")
            return

        # 4. Load Data
        emails_set = set()
        try:
            header_df = pd.read_csv(upload.file_path.path, nrows=0)
            target_col = next((c for c in header_df.columns if 'mail' in c.lower()), header_df.columns[0])
            
            for chunk in pd.read_csv(upload.file_path.path, chunksize=5000, usecols=[target_col]):
                clean_chunk = chunk[target_col].dropna().astype(str).str.lower().str.strip()
                emails_set.update(clean_chunk)
        except Exception as e:
            print(f"--- [CSV ERROR] {e} ---")
            upload.status = 'FAILED'
            upload.save(using=account_db_name)
            return

        unique_records = len(emails_set)
        upload.original_record_count = unique_records
        upload.save(using=account_db_name)

        # 5. Redis Filtering (UPDATED TO USE GLOBAL CHECKS)
        filtered_emails = []
        filtered_bounces = 0
        filtered_unsubs = 0

        for email in list(emails_set):
            # Check Global Lists (No account_id needed)
            if check_list(email, list_type='BOUNCE'):
                filtered_bounces += 1
                continue
            if check_list(email, list_type='UNSUB'):
                filtered_unsubs += 1
                continue
            filtered_emails.append(email)

        upload.filtered_bounce_count = filtered_bounces
        upload.filtered_unsub_count = filtered_unsubs
        upload.save(using=account_db_name)

        emails_to_verify = filtered_emails
        emails_count = len(emails_to_verify)
        
        COST_PER_EMAIL = 0.5
        total_cost = emails_count * COST_PER_EMAIL

        if initial_credits < total_cost:
            upload.status = 'FAILED'
            upload.save(using=account_db_name)
            print(f"--- [CREDIT ERROR] Insufficient credits for {account_id}. ---")
            return

        with main_db_conn.cursor() as cursor:
            cursor.execute(
                "UPDATE accounts_account SET credits_available = credits_available - %s WHERE account_id = %s", 
                [total_cost, account_id]
            )

        # 6. Verification Loop
        batch_size = 500
        current_batch = []
        final_valid_count = 0
        
        for i, email in enumerate(emails_to_verify):
            # Simulated Logic
            syntax = '@' in email and '.' in email
            domain = True
            smtp = True
            disposable = False
            
            final_status = 'INVALID'
            if syntax and domain and smtp and not disposable:
                final_status = 'VALID'
                final_valid_count += 1
            
            current_batch.append(VerificationResult(
                file=upload, email=email, syntax_status=syntax, domain_status=domain,
                smtp_status=smtp, greylisted=False, smart_verify_status=True,
                free_mail_status=False, disposable_status=disposable,
                catch_all_status=False, role_based_status=False,
                final_status=final_status
            ))

            if len(current_batch) >= batch_size:
                VerificationResult.objects.using(account_db_name).bulk_create(current_batch)
                current_batch = []
                upload.unique_record_count = final_valid_count
                upload.save(using=account_db_name)

        if current_batch:
            VerificationResult.objects.using(account_db_name).bulk_create(current_batch)

        upload.unique_record_count = final_valid_count
        upload.status = 'COMPLETED'
        upload.completed_at = timezone.now()
        upload.save(using=account_db_name)
        print(f"--- [COMPLETED] File {file_id} processed successfully. ---")

        archive_file_results.delay(upload.file_id, account_id)

    except Exception as e:
        print(f"--- [FATAL TASK ERROR] {e} ---")
        if account_db_name and 'upload' in locals():
             try:
                 upload.status = 'FAILED'
                 upload.save(using=account_db_name)
             except: pass

@shared_task
def archive_file_results(file_id, account_id):
    pass