from django.core.management.base import BaseCommand
from files.models import BouncedEmail, UnsubscribedEmail
from core.redis_utils import add_to_list, REDIS_NODES_CONFIG
import redis

class Command(BaseCommand):
    help = 'Refreshes Redis Cache from the Database (Run after adding new Shards)'

    def handle(self, *args, **kwargs):
        self.stdout.write("--- STARTING REDIS REFRESH ---")
        
        # Optional: Flush existing Redis data to clean up "orphaned" keys
        # (Keys that are no longer valid on their old shards)
        self.stdout.write("1. Flushing old Redis data...")
        for node_name, config in REDIS_NODES_CONFIG.items():
            try:
                r = redis.Redis(host=config['host'], port=config['port'], db=config['db'])
                r.flushdb()
                self.stdout.write(f"   - Flushed {node_name}")
            except Exception as e:
                self.stdout.write(f"   - Failed to flush {node_name}: {e}")

        # Re-populate Bounced Emails
        self.stdout.write("\n2. Loading Bounced Emails...")
        bounce_qs = BouncedEmail.objects.using('default').all()
        count = 0
        for obj in bounce_qs.iterator(chunk_size=5000):
            # add_to_list will automatically find the NEW correct shard
            add_to_list(obj.email, 'BOUNCE', obj.uploaded_by_user_id)
            count += 1
            if count % 10000 == 0:
                self.stdout.write(f"   - Processed {count}...")
        self.stdout.write(f"   ✔ Loaded {count} Bounced Emails.")

        # Re-populate Unsubscribed Emails
        self.stdout.write("\n3. Loading Unsubscribed Emails...")
        unsub_qs = UnsubscribedEmail.objects.using('default').all()
        count = 0
        for obj in unsub_qs.iterator(chunk_size=5000):
            add_to_list(obj.email, 'UNSUB', obj.uploaded_by_user_id)
            count += 1
            if count % 10000 == 0:
                self.stdout.write(f"   - Processed {count}...")
        self.stdout.write(f"   ✔ Loaded {count} Unsubscribed Emails.")

        self.stdout.write("\n=== REDIS REFRESH COMPLETE ===")