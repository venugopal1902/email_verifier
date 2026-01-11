import time
import re
import socket
import smtplib
import dns.resolver
import pandas as pd
import concurrent.futures  # <--- NEW: For Parallel Execution
from celery import shared_task
from django.db import connections
from django.conf import settings
from django.utils import timezone

# Import core Redis utilities
from core.redis_utils import check_list 
from files.models import FileUpload, VerificationResult
from core.db_routers import MAIN_DB_LABEL

# --- HELPER FUNCTIONS ---

def is_valid_format(email):
    regex = r"(^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$)"
    return re.match(regex, email) is not None

def get_domain(email):
    try:
        return email.split('@')[1]
    except IndexError:
        return ""

def domain_exists(domain):
    if not domain: return False
    try:
        socket.gethostbyname(domain)
        return True
    except socket.gaierror:
        return False

def get_mx_records(domain):
    try:
        records = dns.resolver.resolve(domain, "MX")
        return sorted([(r.preference, str(r.exchange)) for r in records], key=lambda x: x[0])
    except Exception:
        return []

def smtp_mailbox_check(email, mx_records):
    """
    Returns: 'VALID', 'INVALID', or 'UNKNOWN'
    """
    from_address = "verify@example.com"
    timeout_sec = 2 
    
    for _, mx_host in mx_records:
        try:
            server = smtplib.SMTP(mx_host, 25, timeout=timeout_sec)
            server.helo("example.com")
            server.mail(from_address)
            code, message = server.rcpt(email)
            server.quit()

            if code == 250: return 'VALID'
            if code == 550: return 'INVALID' 
            
        except (socket.timeout, ConnectionRefusedError, OSError):
            continue 
        except Exception:
            continue

    return 'UNKNOWN' 

def verify_single_email_logic(email):
    """
    Standalone function to verify one email. 
    Returns a dict of results.
    """
    syntax = is_valid_format(email)
    domain_check = False
    mx_check = False
    smtp_status = 'UNKNOWN'
    
    final_status = 'INVALID'

    if syntax:
        domain = get_domain(email)
        if domain_exists(domain):
            domain_check = True
            mx_records = get_mx_records(domain)
            if mx_records:
                mx_check = True
                smtp_status = smtp_mailbox_check(email, mx_records)
    
    # Decision Logic
    if smtp_status == 'VALID':
        final_status = 'VALID'
    elif smtp_status == 'INVALID':
        final_status = 'INVALID'
    elif smtp_status == 'UNKNOWN' and mx_check:
        final_status = 'VALID'  # Risky/Accept all
    else:
        final_status = 'INVALID'
        
    return {
        'email': email,
        'syntax': syntax,
        'domain': domain_check,
        'smtp': (smtp_status == 'VALID'),
        'final': final_status
    }

# --- MAIN TASK ---

@shared_task(bind=True)
def process_verification_pipeline(self, file_id, account_id):
    print(f"--- [TASK STARTED] File: {file_id} ---")
    account_db_name = None
    
    def ensure_account_db_configured(db_name):
        if db_name not in connections.databases:
            if 'default' in settings.DATABASES:
                connections.databases[db_name] = settings.DATABASES['default'].copy()

    try:
        # 1. Connect and Setup DB
        main_db_conn = connections[MAIN_DB_LABEL]
        with main_db_conn.cursor() as cursor:
            cursor.execute("SELECT database_name, credits_available FROM accounts_account WHERE account_id = %s", [account_id])
            account_data = cursor.fetchone()
            if not account_data: return

        account_db_name = account_data[0]
        initial_credits = account_data[1]
        ensure_account_db_configured(account_db_name)
        
        # 2. Retrieve Upload
        try:
            upload = FileUpload.objects.using(account_db_name).get(file_id=file_id)
            upload.status = 'PROCESSING'
            upload.started_at = timezone.now()
            upload.save(using=account_db_name)
        except FileUpload.DoesNotExist:
            return

        # 3. Load Data
        emails_set = set()
        try:
            header_df = pd.read_csv(upload.file_path.path, nrows=0)
            target_col = next((c for c in header_df.columns if 'mail' in c.lower()), header_df.columns[0])
            for chunk in pd.read_csv(upload.file_path.path, chunksize=5000, usecols=[target_col]):
                clean_chunk = chunk[target_col].dropna().astype(str).str.lower().str.strip()
                emails_set.update(clean_chunk)
        except Exception:
            upload.status = 'FAILED'
            upload.save(using=account_db_name)
            return

        # 4. Filter (Redis)
        filtered_emails = []
        filtered_bounces = 0
        filtered_unsubs = 0

        for email in list(emails_set):
            if check_list(email, list_type='BOUNCE'):
                filtered_bounces += 1
                continue
            if check_list(email, list_type='UNSUB'):
                filtered_unsubs += 1
                continue
            filtered_emails.append(email)

        upload.filtered_bounce_count = filtered_bounces
        upload.filtered_unsub_count = filtered_unsubs
        upload.unique_record_count = 0 
        upload.invalid_record_count = 0
        upload.save(using=account_db_name)

        emails_to_verify = filtered_emails
        emails_count = len(emails_to_verify)
        
        # 5. Credits
        COST_PER_EMAIL = 0.1
        total_cost = emails_count * COST_PER_EMAIL
        if initial_credits < total_cost:
            upload.status = 'FAILED'
            upload.save(using=account_db_name)
            return
        with main_db_conn.cursor() as cursor:
            cursor.execute("UPDATE accounts_account SET credits_available = credits_available - %s WHERE account_id = %s", [total_cost, account_id])

        # 6. PARALLEL VERIFICATION LOOP (The Fix)
        # Use ThreadPoolExecutor to run 50 checks at once within this single task
        
        batch_size = 500
        current_batch = []
        final_valid_count = 0
        final_invalid_count = 0
        
        # Max Workers: 50 is safe for local. Cloud can go higher.
        print(f"--- STARTING THREAD POOL FOR {len(emails_to_verify)} EMAILS ---")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=500) as executor:
            # Submit all emails to the pool
            future_to_email = {executor.submit(verify_single_email_logic, email): email for email in emails_to_verify}
            
            for i, future in enumerate(concurrent.futures.as_completed(future_to_email)):
                try:
                    res = future.result()
                    
                    if res['final'] == 'VALID':
                        final_valid_count += 1
                    else:
                        final_invalid_count += 1
                    
                    # Create Result Object
                    current_batch.append(VerificationResult(
                        file=upload, email=res['email'], 
                        syntax_status=res['syntax'], domain_status=res['domain'],
                        smtp_status=res['smtp'],
                        final_status=res['final']
                    ))
                    
                    # Batch Update DB
                    if len(current_batch) >= batch_size or (i % 20 == 0):
                        # Update stats frequently for UI
                        upload.unique_record_count = final_valid_count
                        upload.invalid_record_count = final_invalid_count
                        upload.save(using=account_db_name)
                        
                    if len(current_batch) >= batch_size:
                        VerificationResult.objects.using(account_db_name).bulk_create(current_batch)
                        current_batch = []
                        
                except Exception as exc:
                    print(f"Generated an exception: {exc}")

        # Save remaining results
        if current_batch:
            VerificationResult.objects.using(account_db_name).bulk_create(current_batch)

        # Final Status
        upload.unique_record_count = final_valid_count
        upload.invalid_record_count = final_invalid_count
        upload.status = 'COMPLETED'
        upload.completed_at = timezone.now()
        upload.save(using=account_db_name)
        
        print(f"--- [COMPLETED] Valid: {final_valid_count}, Invalid: {final_invalid_count} ---")
        
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