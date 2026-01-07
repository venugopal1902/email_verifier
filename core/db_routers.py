from django.conf import settings

MAIN_DB_MODELS = [
    'account',
    'accountuser',
]

MAIN_DB_LABEL = settings.MAIN_DB_LABEL

class AccountRouter:
    """
    Router to control database operations for Multi-Tenancy.
    """

    def db_for_read(self, model, **hints):
        app_label = model._meta.app_label
        model_name = model._meta.model_name
        
        if model_name in MAIN_DB_MODELS or app_label == 'auth' or app_label == 'contenttypes':
            return MAIN_DB_LABEL
        
        if hints.get('account_db_name'):
            return hints['account_db_name']

        return MAIN_DB_LABEL 

    def db_for_write(self, model, **hints):
        return self.db_for_read(model, **hints)

    def allow_relation(self, obj1, obj2, **hints):
        """
        Determine if a relation is allowed between two objects.
        """
        # 1. Allow if both objects belong to the 'files' app (Tenant Data)
        # This fixes the "Cannot assign" error for aliased databases (acct_002_db vs default)
        if obj1._meta.app_label == 'files' and obj2._meta.app_label == 'files':
            return True

        # 2. Allow if both objects are logically in the same database
        if obj1._state.db == obj2._state.db:
            return True
        
        # 3. Allow relations involving Main DB models (like User) if needed
        # (Optional, depending on strictness)
        
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == 'files':
            return True 
        return None