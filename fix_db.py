import os
import glob
import django
import uuid
from django.conf import settings
from django.db import connection
from django.core.management import call_command

# 1. Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

print("\n=== 🛠️ STARTING SMART DATABASE REPAIR ===\n")

# --- PHASE 1: DATABASE RESET (DEFAULT DB) ---
print("[1/4] Repairing Default Database Tables...")
tables_to_drop = ["verification_results", "file_uploads", "bounce_emails", "unsubscribed_emails"]
with connection.cursor() as cursor:
    for table in tables_to_drop:
        try:
            cursor.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")
        except Exception: pass
    # Clear migration history
    cursor.execute("DELETE FROM django_migrations WHERE app='files';")

# --- PHASE 2: REGENERATE MIGRATIONS ---
print("[2/4] Regenerating Migrations...")
migration_dir = os.path.join(settings.BASE_DIR, 'files', 'migrations')
for f in glob.glob(os.path.join(migration_dir, "*.py")):
    if not f.endswith("__init__.py"):
        os.remove(f)

try:
    call_command('makemigrations', 'files')
    call_command('migrate')
    print("    ✔ Default Database is now healthy (Tables created).")
except Exception as e:
    print(f"    ❌ Migration Failed: {e}")
    exit(1)

# --- PHASE 3: FIX ACCOUNT ROUTING (THE CRITICAL FIX) ---
print("[3/4] Routing 'owner@acme.com' to Default Database...")
from accounts.models import Account
from django.contrib.auth import get_user_model

User = get_user_model()
try:
    # 1. Find the main user
    user = User.objects.get(email='owner@acme.com')
    my_account = user.account
    print(f"    ℹ User found. Current DB: {my_account.database_name}")

    if my_account.database_name != 'default':
        # 2. Check if 'default' is taken by someone else
        conflict_account = Account.objects.filter(database_name='default').first()
        if conflict_account:
            # Rename the conflicting account's DB to free up the name
            new_name = f"backup_{uuid.uuid4().hex[:8]}"
            conflict_account.database_name = new_name
            conflict_account.save()
            print(f"    ⚠ Moved conflicting account to '{new_name}'")

        # 3. Assign 'default' to our main user
        my_account.database_name = 'default'
        my_account.save()
        print("    ✔ SUCCESS: Your account now points to the Default Database.")
    else:
        print("    ✔ Account is already correctly configured.")

except User.DoesNotExist:
    print("    ⚠ User 'owner@acme.com' not found. Please create this user first.")
except Exception as e:
    print(f"    ❌ Error configuring account: {e}")

print("\n=== ✅ REPAIR COMPLETE ===")