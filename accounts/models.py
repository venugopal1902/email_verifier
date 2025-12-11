from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.utils import timezone

# --- Main/Global Database Models (as per PRD Section 2.2 A) ---

class AccountManager(BaseUserManager):
    # Simplified manager for creating users (including Admin for the main DB)
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        extra_fields.setdefault('role', 'ADMIN') # Custom role field for system admin
        return self.create_user(email, password, **extra_fields)


class Account(models.Model):
    """ACCOUNTS - Stores global account information (Main DB)"""
    STATUS_CHOICES = [('ACTIVE', 'Active'), ('SUSPENDED', 'Suspended')]

    account_id = models.CharField(max_length=50, primary_key=True)
    account_name = models.CharField(max_length=255)
    admin_created_by = models.ForeignKey(
        'AccountUser', 
        on_delete=models.SET_NULL, 
        null=True, 
        related_name='created_accounts'
    )
    max_users = models.IntegerField(default=1)
    credits_available = models.DecimalField(max_digits=12, decimal_places=2, default=0.00) # FR03
    database_name = models.CharField(max_length=100, unique=True, help_text="The dedicated database name for this account.")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='ACTIVE')
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.account_name} ({self.account_id})"
    
    # Custom method to check credits (FR32)
    def has_sufficient_credits(self, required_credits):
        return self.credits_available >= required_credits

class AccountUser(AbstractBaseUser, PermissionsMixin):
    """ACCOUNT_USERS - Stores users and their mapping to an Account (Main DB)"""
    ROLE_CHOICES = [
        ('ADMIN', 'System Admin'),
        ('OWNER', 'Account Owner'),
        ('USER', 'Regular User')
    ]
    
    email = models.EmailField(unique=True)
    account = models.ForeignKey(
        Account, 
        on_delete=models.CASCADE, 
        null=True, 
        blank=True, 
        related_name='users',
        help_text="Null for System Admins."
    )
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='USER')
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = AccountManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    def __str__(self):
        return self.email

    def is_account_owner(self):
        return self.role == 'OWNER'

    def is_system_admin(self):
        return self.role == 'ADMIN'

    class Meta:
        verbose_name = 'Account User'
        verbose_name_plural = 'Account Users'