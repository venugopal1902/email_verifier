from django.core.management.base import BaseCommand
from files.models import BouncedEmail, UnsubscribedEmail
from core.redis_utils import add_to_list

class Command(BaseCommand):
    help = 'Syncs PostgreSQL suppression lists to Redis Shards'

    def handle(self, *args, **kwargs):
        self.stdout.write("Syncing Bounced Emails to Redis Shards...")
        bounces = BouncedEmail.objects.all()
        bounce_count = 0
        for b in bounces:
            add_to_list(b.email, list_type='BOUNCE', user_id=b.uploaded_by_user_id)
            bounce_count += 1
        self.stdout.write(f"Synced {bounce_count} bounced emails.")

        self.stdout.write("Syncing Unsubscribed Emails to Redis Shards...")
        unsubs = UnsubscribedEmail.objects.all()
        unsub_count = 0
        for u in unsubs:
            add_to_list(u.email, list_type='UNSUB', user_id=u.uploaded_by_user_id)
            unsub_count += 1
        self.stdout.write(f"Synced {unsub_count} unsubscribed emails.")

        self.stdout.write(self.style.SUCCESS('Successfully refreshed Redis Shards!'))