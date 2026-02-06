import time
import re
import smtplib
import dns.resolver
import pandas as pd
import subprocess
import logging
import socket
from celery import shared_task, chord
from django.db.models import F
from core.redis_utils import check_list
from files.models import FileUpload

logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
BATCH_SIZE = 500          # Larger batches reduce Redis overhead
DNS_TIMEOUT = 2.0         # Fail fast
MAX_CONCURRENCY = 20      # Greenlets per worker task

# Configure Global DNS
resolver = dns.resolver.Resolver()
resolver.timeout = DNS_TIMEOUT
resolver.lifetime = DNS_TIMEOUT

# ---------------------------------------------------------
# 1. MANAGER TASK (CPU Queue)
# Reads file, counts rows, and triggers the parallel workers
# ---------------------------------------------------------
@shared_task(queue='cpu')
def process_file_initialization(file_id):
    try:
        # 1. Setup
        upload = FileUpload.objects.get(id=file_id)
        upload.status = 'Processing'
        upload.processed_records = 0
        upload.valid_count = 0
        upload.invalid_count = 0
        upload.save()

        file_path = upload.file.path
        total_records = 0

        # 2. Fast Count (Linux/Docker Optimization)
        try:
            # wc -l is instant for large files
            result = subprocess.check_output(['wc', '-l', file_path])
            total_records = int(result.split()[0]) - 1 # Subtract header
        except Exception:
            # Fallback for Windows/Errors
            try:
                df = pd.read_csv(file_path)
                total_records = len(df)
            except:
                total_records = 0
        
        if total_records < 1: total_records = 1
        upload.total_records = total_records
        upload.save()

        # 3. Create the Job List (Signatures)
        # We do NOT run .delay() here. We just create the list.
        task_signatures = []
        
        # Read in chunks (Memory Safe)
        for chunk in pd.read_csv(file_path, chunksize=BATCH_SIZE):
            # Normalize column names to find email
            cols = [c for c in chunk.columns if 'mail' in c.lower()]
            col = cols[0] if cols else chunk.columns[0]
            
            # Extract valid email strings
            emails = chunk[col].dropna().astype(str).tolist()
            
            if emails:
                # Add to list
                task_signatures.append(verify_email_batch.s(file_id, emails))

        # 4. EXECUTE THE CHORD
        # This is the magic. It runs all batches, then GUARANTEES finalize_file runs.
        logger.info(f"Dispatching {len(task_signatures)} tasks for File {file_id}")
        chord(task_signatures)(finalize_file.s(file_id))

    except Exception as e:
        logger.error(f"Initialization Failed for {file_id}: {e}")
        upload = FileUpload.objects.get(id=file_id)
        upload.status = 'Failed'
        upload.save()

# ---------------------------------------------------------
# 2. WORKER TASK (I/O Queue)
# Verifies a batch of emails. DOES NOT check for completion.
# ---------------------------------------------------------
@shared_task(queue='io', bind=True)
def verify_email_batch(self, file_id, email_list):
    valid_count = 0
    invalid_count = 0
    
    # Process batch (using internal gevent pool if needed, or sequential)
    # Since you likely run celery with -P gevent, sequential here is fine 
    # because the Worker itself is already concurrent.
    for email in email_list:
        if verify_single_email(email):
            valid_count += 1
        else:
            invalid_count += 1

    # Atomic Update (Stateless)
    # We allow the database to handle the math safely.
    FileUpload.objects.filter(id=file_id).update(
        processed_records=F('processed_records') + len(email_list),
        valid_count=F('valid_count') + valid_count,
        invalid_count=F('invalid_count') + invalid_count
    )
    
    return True # Return value is passed to the chord callback (optional)

# ---------------------------------------------------------
# 3. FINALIZER TASK (CPU Queue)
# Runs ONLY when all workers are done.
# ---------------------------------------------------------
@shared_task(queue='cpu')
def finalize_file(results, file_id):
    try:
        logger.info(f"Finalizing File {file_id}...")
        upload = FileUpload.objects.get(id=file_id)
        
        # Double check: Ensure 100% stats for UI
        upload.refresh_from_db()
        if upload.processed_records < upload.total_records:
            upload.processed_records = upload.total_records
            
        upload.status = 'Completed'
        upload.save()
        
    except Exception as e:
        logger.error(f"Finalization failed for {file_id}: {e}")

# ---------------------------------------------------------
# 4. HELPER FUNCTIONS
# ---------------------------------------------------------
def verify_single_email(email):
    """ Returns True (Valid) or False (Invalid) """
    try:
        # 1. Syntax
        if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            return False

        domain = email.split('@')[1]

        # 2. DNS MX
        try:
            records = resolver.resolve(domain, 'MX')
            mx_record = str(records[0].exchange)
        except:
            return False # No Domain = Invalid

        # 3. SMTP Ping
        server = smtplib.SMTP(timeout=3) # Short timeout
        server.set_debuglevel(0)
        
        code = 0
        try:
            server.connect(mx_record)
            server.helo(socket.gethostname())
            server.mail('test@example.com')
            code, _ = server.rcpt(email)
            server.quit()
        except:
            return False # Connection Error = Invalid/Risky

        # 250 = OK
        return code == 250

    except Exception:
        return False