import time
import re
import smtplib
import dns.resolver
import pandas as pd
import subprocess
import logging
import functools
import traceback
from celery import shared_task, chord
from django.db.models import F
from files.models import FileUpload
import socket

# --- DEBUG DECORATOR (Copy this) ---
def log_debug(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        print(f"\n[DEBUG] >>> STARTING: {func.__name__}")
        print(f"[DEBUG] Args: {args}")
        try:
            result = func(*args, **kwargs)
            print(f"[DEBUG] <<< SUCCESS: {func.__name__}\n")
            return result
        except Exception as e:
            print(f"\n[CRITICAL FAILURE] inside {func.__name__}")
            print(f"ERROR: {str(e)}")
            traceback.print_exc()  # PRINTS THE EXACT ERROR LINE
            raise e
    return wrapper
# -----------------------------------

logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
BATCH_SIZE = 500
DNS_TIMEOUT = 2.0

# Configure Global DNS
resolver = dns.resolver.Resolver()
resolver.timeout = DNS_TIMEOUT
resolver.lifetime = DNS_TIMEOUT

# ---------------------------------------------------------
# 1. MANAGER TASK
# ---------------------------------------------------------
@shared_task(queue='cpu')
@log_debug  # <--- THIS WILL PRINT LOGS
def process_file_initialization(file_id):
    # 1. Fetch File
    print(f"[STEP 1] Fetching FileUpload with ID: {file_id}")
    upload = FileUpload.objects.get(id=file_id)
    
    upload.status = 'Processing'
    upload.processed_records = 0
    upload.save()

    try:
        file_path = upload.file.path
    except AttributeError:
        # Sometimes people name the field 'file_path' instead of 'file'
        file_path = upload.file_path.path
        
    print(f"[STEP 2] File Path found: {file_path}")

    # 2. Count Rows
    total_records = 0
    try:
        print("[STEP 3] Attempting fast count (wc -l)...")
        result = subprocess.check_output(['wc', '-l', file_path])
        total_records = int(result.split()[0]) - 1 
    except Exception as e:
        print(f"[WARN] wc -l failed ({e}), switching to pandas...")
        df = pd.read_csv(file_path)
        total_records = len(df)
    
    if total_records < 1: total_records = 1
    
    print(f"[STEP 4] Total Records: {total_records}")
    upload.total_records = total_records
    upload.save()

    # 3. Create Tasks
    print("[STEP 5] Reading CSV and creating batches...")
    task_signatures = []
    
    batch_count = 0
    for chunk in pd.read_csv(file_path, chunksize=BATCH_SIZE):
        # Normalize Headers
        cols = [c for c in chunk.columns if 'mail' in c.lower()]
        col = cols[0] if cols else chunk.columns[0]
        
        emails = chunk[col].dropna().astype(str).tolist()
        
        if emails:
            # Create a signature (task definition)
            task_signatures.append(verify_email_batch.s(file_id, emails))
            batch_count += 1

    print(f"[STEP 6] Batches Created: {batch_count}. Dispatching Chord...")
    
    # 4. Fire Chord
    if task_signatures:
        chord(task_signatures)(finalize_file.s(file_id))
        print("[STEP 7] Chord Dispatched Successfully.")
    else:
        print("[WARN] No emails found in file!")
        upload.status = 'Failed'
        upload.save()

# ---------------------------------------------------------
# 2. WORKER TASK
# ---------------------------------------------------------
@shared_task(queue='io', bind=True)
def verify_email_batch(self, file_id, email_list):
    # We remove @log_debug here to avoid spamming logs 
    # (since this runs 1000s of times)
    valid_count = 0
    invalid_count = 0
    
    for email in email_list:
        if verify_single_email(email):
            valid_count += 1
        else:
            invalid_count += 1

    FileUpload.objects.filter(id=file_id).update(
        processed_records=F('processed_records') + len(email_list),
        valid_count=F('valid_count') + valid_count,
        invalid_count=F('invalid_count') + invalid_count
    )
    return True

# ---------------------------------------------------------
# 3. FINALIZER TASK
# ---------------------------------------------------------
@shared_task(queue='cpu')
@log_debug # <--- Add logs here too
def finalize_file(results, file_id):
    print(f"[FINALIZER] Finishing File {file_id}...")
    upload = FileUpload.objects.get(id=file_id)
    
    upload.refresh_from_db()
    
    # Sync counts for UI prettiness
    if upload.processed_records < upload.total_records:
        upload.processed_records = upload.total_records
        
    upload.status = 'Completed'
    upload.save()
    print("[FINALIZER] Status set to COMPLETED.")

# ---------------------------------------------------------
# 4. HELPER
# ---------------------------------------------------------
def verify_single_email(email):
    try:
        # Syntax
        if not re.match(r"[^@]+@[^@]+\.[^@]+", email): return False
        
        domain = email.split('@')[1]
        
        # DNS
        try:
            records = resolver.resolve(domain, 'MX')
            mx_record = str(records[0].exchange)
        except: return False

        # SMTP
        try:
            server = smtplib.SMTP(timeout=3)
            server.set_debuglevel(0)
            server.connect(mx_record)
            server.helo(socket.gethostname())
            server.mail('test@example.com')
            code, _ = server.rcpt(email)
            server.quit()
            return code == 250
        except: return False
    except: return False