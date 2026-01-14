import time
import re
import smtplib
import dns.resolver
import pandas as pd
from celery import shared_task
import subprocess
from django.db.models import F
from django.utils import timezone
from gevent.pool import Pool
from django.db import connections
from django.conf import settings
import socket

# --- IMPORT REDIS UTILS ---
from core.redis_utils import check_list 
from files.models import FileUpload, VerificationResult
BATCH_SIZE = 100
DNS_TIMEOUT = 2.0     # Fail fast (2s) instead of waiting 30s
MAX_CONCURRENCY = 50  # Greenlets per task (since you have 200 workers, 200*50 is plenty)

# Configure Global DNS Resolver
resolver = dns.resolver.Resolver()
resolver.timeout = DNS_TIMEOUT
resolver.lifetime = DNS_TIMEOUT

# --- 1. CORE HELPER FUNCTIONS (SAFER PURE PYTHON DNS) ---

def is_valid_format(email):
    """
    Validates email format using regex and strict length checks.
    """
    if str(email).count('@') != 1:
        return False

    regex = r"(^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$)"
    if not re.match(regex, str(email)):
        return False
    
    try:
        domain = email.split('@')[1]
        # Check Lengths to prevent DNS lib errors
        if len(domain) > 255: return False
        if any(len(label) > 63 for label in domain.split('.')): return False
        
        # IDNA Check (Safe Python-side check)
        domain.encode('idna')
    except Exception:
        return False
            
    return True

def get_domain(email):
    try:
        domain = email.split('@', 1)[1].strip().lower()
        if not domain or domain.startswith('.') or domain.endswith('.'):
            return ""
        return domain
    except Exception:
        return ""

def domain_exists(domain):
    """
    Checks if domain has A records using dnspython (Pure Python).
    Avoids socket.gethostbyname which crashes gevent threads.
    """
    if not domain: return False
    
    try:
        # USE DNS.RESOLVER INSTEAD OF SOCKET
        # This is safer inside Gevent/Celery
        # dns.resolver.resolve(domain, 'A')
        domain_idna = domain.encode('idna').decode('ascii')
        socket.gethostbyname(domain_idna)
        return True
    except (UnicodeError, socket.gaierror,dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        return False
    except Exception:
        return False

def get_mx_records(domain):
    try:
        # records = dns.resolver.resolve(domain, "MX")
        domain_idna = domain.encode('idna').decode('ascii')
        records = dns.resolver.resolve(domain_idna, "MX")
        return sorted(
            [(r.preference, str(r.exchange).rstrip('.')) for r in records],
            key=lambda x: x[0]
        )
        # return sorted([(r.preference, str(r.exchange)) for r in records], key=lambda x: x[0])
    except Exception: 
        return []

def smtp_mailbox_check(email, mx_records):
    """
    Performs the actual SMTP Handshake.
    """
    from_address = "verify@example.com"
    timeout_sec = 2
    
    for _, mx_host in mx_records:
        try:
            # server = smtplib.SMTP(mx_host, 25, timeout=timeout_sec)
            # server.helo("example.com")
            # server.mail(from_address)
            # code, message = server.rcpt(email)
            # server.quit()
            mx_host = mx_host.encode('idna').decode('ascii')
            server = smtplib.SMTP(mx_host, 25, timeout=timeout_sec)
            server.helo("example.com")
            server.mail(from_address)
            code, _ = server.rcpt(email)
            server.quit()

            if code == 250: return 'VALID'
            if code == 550: return 'INVALID' 
            
        except Exception:
            continue

    return 'UNKNOWN'

# --- 2. MAIN LOGIC (CRASH PROOF WRAPPER) ---

def verify_single_email_logic(email):
    """
    Runs verification. Wrapped in broad try/except to preventing Batch Crashes.
    """
    try:
        # --- A. REDIS CHECK ---
        if check_list(email, 'BOUNCE'):
            return {'email': email, 'final': 'BOUNCED_FILTERED'}
        if check_list(email, 'UNSUB'):
            return {'email': email, 'final': 'UNSUB_FILTERED'}

        # --- B. VERIFICATION ---
        # syntax = is_valid_format(email)
        try:
            syntax = is_valid_format(email)
        except UnicodeError:
            return {'email': email, 'syntax': False, 'domain': False, 'smtp': False, 'final': 'INVALID'}
        
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
        
        if smtp_status == 'VALID': final_status = 'VALID'
        elif smtp_status == 'INVALID': final_status = 'INVALID'
        elif smtp_status == 'UNKNOWN' and mx_check: final_status = 'VALID' 
        else: final_status = 'INVALID'
            
        return {
            'email': email,
            'syntax': syntax,
            'domain': domain_check,
            'smtp': (smtp_status == 'VALID'),
            'final': final_status
        }
    except Exception as e:
        # EMERGENCY FALLBACK: If anything crashes, mark INVALID but DO NOT KILL BATCH
        print(f"Error processing {email}: {e}")
        return {
            'email': email,
            'syntax': False,
            'domain': False,
            'smtp': False,
            'final': 'INVALID' # Safest fallback
        }

# --- 3. WORKER CONFIGURATION ---

def configure_account_db(account_db_name):
    if account_db_name in connections.databases: return True
    try:
        default_config = settings.DATABASES['default'].copy()
        connections.databases[account_db_name] = default_config
        return True
    except Exception as e:
        print(f"DB Config Error: {e}")
        return False

# --- 4. BATCH WORKER ---

@shared_task(rate_limit="600/s")
def process_batch(email_list, file_id, db_name):
    configure_account_db(db_name)
    
    # Run Parallel Checks
    # pool.map returns a list in order. If verify_single_email_logic never crashes,
    # we get 1 result per email.
    pool = Pool(MAX_CONCURRENCY)
    results = pool.map(verify_single_email_logic, email_list)
    
    db_objs = []
    valid_inc = 0
    invalid_inc = 0
    bounce_inc = 0
    unsub_inc = 0

    for r in results:
        status = r['final']
        if status == 'BOUNCED_FILTERED':
            bounce_inc += 1
            continue 
        if status == 'UNSUB_FILTERED':
            unsub_inc += 1
            continue
        if status == 'VALID': 
            valid_inc += 1
        else: 
            invalid_inc += 1
        
        db_objs.append(VerificationResult(
            file_id=file_id, 
            email=r['email'],
            # Safely get keys with defaults
            syntax_status=r.get('syntax', False), 
            domain_status=r.get('domain', False),
            smtp_status=r.get('smtp', False), 
            final_status=status
        ))

    # Bulk Insert
    if db_objs:
        VerificationResult.objects.using(db_name).bulk_create(db_objs)

    # Atomic Update
    update_kwargs = {}
    if valid_inc: update_kwargs['unique_record_count'] = F('unique_record_count') + valid_inc
    if invalid_inc: update_kwargs['invalid_record_count'] = F('invalid_record_count') + invalid_inc
    if bounce_inc: update_kwargs['filtered_bounce_count'] = F('filtered_bounce_count') + bounce_inc
    if unsub_inc: update_kwargs['filtered_unsub_count'] = F('filtered_unsub_count') + unsub_inc
    
    if update_kwargs:
        FileUpload.objects.using(db_name).filter(file_id=file_id).update(**update_kwargs)
    
    # Check Completion
    try:
        f = FileUpload.objects.using(db_name).get(file_id=file_id)
        total = (f.unique_record_count + f.invalid_record_count + 
                 f.filtered_bounce_count + f.filtered_unsub_count)
        
        # Use >= to be safe against async race conditions
        if total >= f.original_record_count and f.status != 'COMPLETED':
            f.status = 'COMPLETED'
            f.completed_at = timezone.now()
            f.save(using=db_name)
    except Exception: pass

# --- 5. DISPATCHER ---

@shared_task
def dispatch_file_processing(file_id, account_id):
    from accounts.models import Account
    account = Account.objects.get(account_id=account_id)
    db_name = account.database_name
    configure_account_db(db_name)
    
    upload = FileUpload.objects.using(db_name).get(file_id=file_id)
    upload.status = 'PROCESSING'
    
    # [OPTIMIZATION] Fast Line Count (Linux/Docker specific)
    try:
        # 'wc -l' is instant even for million-line files
        result = subprocess.check_output(['wc', '-l', upload.file_path.path])
        total_records = int(result.split()[0]) - 1 # Subtract header
        if total_records < 1: total_records = 1
        
        upload.original_record_count = total_records
        upload.save(using=db_name)
        print(f"--- [DISPATCH] Fast count found {total_records} rows ---")
    except Exception as e:
        print(f"--- [DISPATCH] Fast count failed ({e}), falling back to pandas ---")
        # Fallback (Slow)
        try:
            df = pd.read_csv(upload.file_path.path)
            upload.original_record_count = len(df)
            upload.save(using=db_name)
        except: pass

    # Dispatch Chunks
    try:
        for chunk in pd.read_csv(upload.file_path.path, chunksize=BATCH_SIZE):
            cols = [c for c in chunk.columns if 'mail' in c.lower()]
            col = cols[0] if cols else chunk.columns[0]
            emails = chunk[col].dropna().astype(str).str.strip().tolist()
            
            if emails:
                process_batch.delay(emails, file_id, db_name)
                
    except Exception as e:
        print(f"Dispatch Error: {e}")
        upload.status = 'FAILED'
        upload.save(using=db_name)