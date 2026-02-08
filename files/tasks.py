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
from django.conf import settings

# Models
from files.models import FileUpload, VerificationResult
# CRITICAL: Import Account to deduct credits
from accounts.models import Account 

logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
BATCH_SIZE = 100 # Smaller batches are safer for credit deduction
DNS_TIMEOUT = 2.0

# Configure Global DNS
resolver = dns.resolver.Resolver()
resolver.timeout = DNS_TIMEOUT
resolver.lifetime = DNS_TIMEOUT

# --- DEBUG DECORATOR ---
def log_debug(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # print(f"\n[DEBUG] >>> STARTING: {func.__name__}")
        try:
            result = func(*args, **kwargs)
            # print(f"[DEBUG] <<< SUCCESS: {func.__name__}\n")
            return result
        except Exception as e:
            print(f"\n[CRITICAL FAILURE] inside {func.__name__}")
            print(f"ERROR: {str(e)}")
            traceback.print_exc()
            raise e
    return wrapper

# ---------------------------------------------------------
# 1. MANAGER TASK (Splits File)
# ---------------------------------------------------------
@shared_task(queue='cpu')
@log_debug
def process_file_initialization(file_id):
    print(f"[MANAGER] Starting processing for File ID: {file_id}")
    
    try:
        file_obj = FileUpload.objects.get(id=file_id)
        
        # 1. Update Status
        file_obj.status = 'Processing'
        file_obj.save()

        # 2. Read CSV
        # Assuming the file path is valid locally or via volume
        df = pd.read_csv(file_obj.file.path)
        
        # 3. Find Email Column (Case-insensitive search)
        email_col = next((col for col in df.columns if 'email' in col.lower()), None)
        if not email_col:
            # Fallback: Use the first column if no 'email' header found
            email_col = df.columns[0]
        
        # 4. Extract Unique Emails
        emails = df[email_col].dropna().astype(str).unique().tolist()
        total_count = len(emails)
        
        file_obj.total_records = total_count
        file_obj.save()
        print(f"[MANAGER] Found {total_count} emails. Splitting into batches...")

        # 5. Create Batches
        batches = [emails[i:i + BATCH_SIZE] for i in range(0, total_count, BATCH_SIZE)]
        
        # 6. Dispatch Workers (Chord)
        # The 'process_batch' task will run in parallel.
        # Once ALL are done, 'finalize_file' will run.
        task_group = chord(
            (process_batch.s(batch, file_id) for batch in batches),
            finalize_file.s(file_id)
        )
        task_group.apply_async()
        
    except Exception as e:
        print(f"[MANAGER ERROR] {e}")
        if 'file_obj' in locals():
            file_obj.status = 'Failed'
            file_obj.save()
        raise e

# ---------------------------------------------------------
# 2. WORKER TASK (Verifies & Deducts Credits)
# ---------------------------------------------------------
@shared_task(queue='cpu')
@log_debug
def process_batch(email_batch, file_id):
    """
    Verifies a list of emails AND deducts credits for them.
    """
    results = []
    valid_count = 0
    invalid_count = 0
    
    file_obj = FileUpload.objects.get(id=file_id)

    # --- CREDIT DEDUCTION (New Logic) ---
    try:
        # We assume 1 email = 1 credit.
        cost = len(email_batch)*0.2 
        
        # Use F() expression for atomic update (prevents race conditions)
        updated_rows = Account.objects.filter(user=file_obj.uploaded_by).update(
            credits_available=F('credits_available') - cost
        )
        
        # Optional: Check if credits went negative (allow debt? or stop?)
        # For now, we assume we allow it or checked beforehand.
        print(f"[WORKER] Deducted {cost} credits for User {file_obj.uploaded_by.id}")
        
    except Exception as e:
        print(f"[WORKER WARNING] Could not deduct credits: {e}")
        # We continue processing even if credit deduction fails, 
        # but you might want to return here if strict payment is required.

    # --- VERIFICATION LOOP ---
    for email in email_batch:
        is_valid = verify_single_email(email)
        status_text = 'Valid' if is_valid else 'Invalid'
        
        if is_valid:
            valid_count += 1
        else:
            invalid_count += 1

        # Save Result to DB
        VerificationResult.objects.create(
            file_id=file_id,
            email=email,
            status=status_text,
            final_status=status_text # For CSV export
        )
        
    # Update File Progress (Atomic increment)
    FileUpload.objects.filter(id=file_id).update(
        processed_records=F('processed_records') + len(email_batch),
        valid_count=F('valid_count') + valid_count,
        invalid_count=F('invalid_count') + invalid_count
    )
    
    return f"Processed {len(email_batch)}"

# ---------------------------------------------------------
# 3. FINALIZER TASK
# ---------------------------------------------------------
@shared_task(queue='cpu')
@log_debug
def finalize_file(results, file_id):
    print(f"[FINALIZER] Finishing File {file_id}...")
    upload = FileUpload.objects.get(id=file_id)
    
    # Refresh to get the latest atomic counts
    upload.refresh_from_db()
    
    # Final cleanup of counts
    if upload.processed_records < upload.total_records:
        upload.processed_records = upload.total_records
        
    upload.status = 'Completed'
    upload.save()
    print("[FINALIZER] Status set to COMPLETED.")

# ---------------------------------------------------------
# 4. HELPER: SINGLE EMAIL VERIFICATION
# ---------------------------------------------------------
def verify_single_email(email):
    try:
        # 1. Syntax Check
        if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            return False
        
        domain = email.split('@')[1]
        
        # 2. DNS MX Check
        try:
            records = resolver.resolve(domain, 'MX')
            mx_record = str(records[0].exchange)
        except:
            return False

        # 3. SMTP Handshake (Ping)
        try:
            # Connect to the mail server
            server = smtplib.SMTP(timeout=3)
            server.set_debuglevel(0)
            server.connect(mx_record)
            server.helo(socket.gethostname()) # Polite handshake
            
            # Ask: "Does this email exist?"
            server.mail('test@example.com')
            code, message = server.rcpt(email)
            server.quit()
            
            # Code 250 means OK (Exists)
            if code == 250:
                return True
            else:
                return False
        except:
            return False # SMTP connect failed
            
    except Exception:
        return False