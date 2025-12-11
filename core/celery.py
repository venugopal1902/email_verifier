import os
from celery import Celery

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

# Create a Celery application instance.
# The name 'core' matches the Django project name.
app = Celery('core')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all Celery settings must have a CELERY_ prefix
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django app configs.
# This ensures that tasks defined in files/tasks.py are found automatically.
app.autodiscover_tasks()

@app.task(bind=True)
def debug_task(self):
    """A dummy task to verify the worker is running."""
    print(f'Request: {self.request!r}')