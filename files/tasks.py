import time
import re
import smtplib
import dns.resolver
import pandas as pd
import logging
import functools
import traceback
import socket
from celery import shared_task, chord
from django.db.models import F
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.db import connections
from functools import lru_cache

# Imports
from files.models import FileUpload, VerificationResult
from accounts.models import Account 
from core.redis_utils import check_list  # <--- YOUR REDIS UTILITY

User = get_user_model()
logger = logging.getLogger(__name__)

# --- CONFIG ---
BATCH_SIZE = 70       # Keep small for concurrency
DNS_TIMEOUT = 4.0      
SMTP_TIMEOUT = 4.0
CACHE_SIZE = 4096     

# DNS Setup
resolver = dns.resolver.Resolver()
resolver.timeout = DNS_TIMEOUT
resolver.lifetime = DNS_TIMEOUT

def log_debug(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            print(f"\n[CRITICAL FAILURE] {func.__name__}: {e}")
            traceback.print_exc()
            raise e
    return wrapper

# ---------------------------------------------------------
# 1. OPTIMIZED DNS LOOKUP (Cached)
# ---------------------------------------------------------
@lru_cache(maxsize=CACHE_SIZE)
def get_mx_record_cached(domain):
    try:
        try:
            domain.encode('idna')
        except UnicodeError:
            return None

        records = resolver.resolve(domain, 'MX')
        if not records: return None
        # Sort by preference to get the primary server
        records = sorted(records, key=lambda r: r.preference)
        return str(records[0].exchange).rstrip('.')
    except Exception as e:
        # print(f"[DNS LOOKUP FAILED] {domain}: {e}") # Optional: Uncomment to debug DNS
        return None

# ---------------------------------------------------------
# 2. MANAGER TASK (CPU - Now Handles Filtering)
# ---------------------------------------------------------
# ... imports remain the same ...

@shared_task(queue='cpu')
@log_debug
def process_file_initialization(file_id):
    print(f"[MANAGER] Processing File ID: {file_id}")
    
    try:
        file_obj = FileUpload.objects.get(file_id=file_id)
        file_obj.status = 'Processing'
        file_obj.started_at = timezone.now()
        file_obj.save()

        # Read CSV
        try:
            # Try standard UTF-8 first
            df = pd.read_csv(file_obj.file_path.path, dtype=str)
        except UnicodeDecodeError:
            print("[MANAGER] UTF-8 failed, retrying with Latin-1...")
            # Fallback to Latin-1 (common for Excel/Legacy CSVs)
            df = pd.read_csv(file_obj.file_path.path, dtype=str, encoding='latin-1')
        
        # --- FIX: Smarter Column Detection ---
        # Look for 'mail' (covers 'email', 'mail', 'Email Address')
        # If not found, look for '@' in the first row's values to guess the column
        email_col = None
        
        # 1. Try finding 'mail' in headers
        for col in df.columns:
            if 'mail' in col.lower():
                email_col = col
                break
        
        # 2. Fallback: Use the first column
        if not email_col:
            email_col = df.columns[0]

        print(f"[DEBUG] Selected Column: {email_col}")

        # Clean & Deduplicate
        emails = df[email_col].dropna().astype(str).str.lower().str.strip().unique().tolist()
        total_count = len(emails)
        
        # --- NEW: Filter Suppression Lists via Redis (CPU Intensive) ---
        filtered_emails = []
        filtered_bounces = 0
        filtered_unsubs = 0

        for email in emails:
            if check_list(email, list_type='UNSUB'):
                filtered_unsubs += 1
                continue
            if check_list(email, list_type='BOUNCE'):
                filtered_bounces += 1
                continue
            
            filtered_emails.append(email)

        # Update stats
        file_obj.total_records = total_count
        file_obj.filtered_bounce_count = filtered_bounces
        file_obj.filtered_unsub_count = filtered_unsubs
        file_obj.processed_records = filtered_bounces + filtered_unsubs 
        file_obj.save()

        # Batch Processing
        if filtered_emails:
            batches = [filtered_emails[i:i + BATCH_SIZE] for i in range(0, len(filtered_emails), BATCH_SIZE)]
            chord(
                (process_batch_io.s(batch, file_id) for batch in batches),
                finalize_file.s(file_id)
            ).apply_async()
        else:
            finalize_file.s([], file_id).apply_async()
        
    except Exception as e:
        print(f"[MANAGER ERROR] {e}")
        FileUpload.objects.filter(file_id=file_id).update(status='Failed')
        raise e
# ---------------------------------------------------------
# 3. IO BATCH WORKER (Pure Network Verification)
# ---------------------------------------------------------
@shared_task(queue='io') 
@log_debug
def process_batch_io(email_batch, file_id):
    """
    Now purely focuses on DNS/SMTP checks.
    Suppression checks are already done in the Manager task.
    """
    if not FileUpload.objects.filter(file_id=file_id).exists():
        return "Aborted"

    try:
        valid_count = 0
        invalid_count = 0
        results_to_create = []
        
        for email in email_batch:
            # Direct verification (Suppression already checked)
            is_valid = verify_single_email(email)
            status_text = 'Valid' if is_valid else 'Invalid'
            
            if is_valid: valid_count += 1
            else: invalid_count += 1
            
            results_to_create.append(
                VerificationResult(
                    file_id=file_id, 
                    email=email,
                    final_status=status_text
                )
            )

        VerificationResult.objects.bulk_create(results_to_create)

        # Update Stats
        FileUpload.objects.filter(file_id=file_id).update(
            processed_records=F('processed_records') + len(email_batch),
            unique_record_count=F('unique_record_count') + valid_count,   
            invalid_record_count=F('invalid_record_count') + invalid_count 
        )

        deduct_credits.delay(file_id, len(email_batch))
        
        return f"Processed {len(email_batch)}"

    finally:
        connections.close_all()

# ---------------------------------------------------------
# 4. HELPER
# ---------------------------------------------------------
def _is_valid_email_format(email):
    if not email: return False
    email = str(email).strip()
    return re.match(r"^[\w\.-]+@[\w\.-]+\.\w+$", email) is not None

def verify_single_email(email):
    if not _is_valid_email_format(email): return False
    try:
        domain = email.split('@')[1]
        
        # 1. Cached DNS Check
        mx_record = get_mx_record_cached(domain)
        if not mx_record: return False

        # 2. SMTP Check
        # try:
        #     server = smtplib.SMTP(timeout=SMTP_TIMEOUT)
        #     server.set_debuglevel(0)
        #     server.connect(mx_record)
        #     server.helo(socket.gethostname())
        #     server.mail('test@example.com')
        #     code, _ = server.rcpt(email)
        #     server.quit()
        #     if code == 250:
        #         return True
        #     else:
        #         print(f"[SMTP REJECT] {email} - Code: {code}")
        #         return False
        # except Exception as e: 
        #     print(f"[SMTP CONNECT ERROR] {email} (MX: {mx_record}): {e}")
        #     return False
        return True  # For now, we trust DNS results. Uncomment SMTP for stricter validation.   
    except Exception as e: 
        print(f"[SYSTEM ERROR] {email}: {e}")
        return False
# ---------------------------------------------------------
# 5. CREDIT DEDUCTION
# ---------------------------------------------------------
@shared_task(queue='cpu')
def deduct_credits(file_id, count):
    try:
        cost = count * 0.2
        file_obj = FileUpload.objects.only('uploaded_by_user_id').get(file_id=file_id)
        
        # Optimized Update
        Account.objects.filter(users__id=file_obj.uploaded_by_user_id).update(
             credits_available=F('credits_available') - cost
        )
    except Exception as e:
        print(f"[CREDIT ERROR] {e}")

# ---------------------------------------------------------
# 6. FINALIZER TASK
# ---------------------------------------------------------
@shared_task(queue='cpu')
@log_debug
def finalize_file(results, file_id):
    print(f"[FINALIZER] Finishing File {file_id}...")
    try:
        upload = FileUpload.objects.get(file_id=file_id)
        if upload.processed_records < upload.total_records:
            upload.processed_records = upload.total_records
            
        upload.status = 'Completed'
        upload.completed_at = timezone.now()
        upload.save()
    except Exception as e:
        print(f"[FINALIZER ERROR] {e}")