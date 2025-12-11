from django.conf import settings

# List of all models that belong to the global MAIN database (ACCOUNTS, ADMIN, etc.)
MAIN_DB_MODELS = [
    # store model names in lowercase to match model._meta.model_name
    'account',
    'accountuser',
    # Add other main DB models here like 'admin', 'globallogs'
]

# The default database label from the .env file
MAIN_DB_LABEL = settings.MAIN_DB_LABEL

class AccountRouter:
    """
    A router to control all database operations on models that need
    to be isolated into their own per-account database.
    
    This fulfills the FR07: Each account shall have its own database.
    """

    def db_for_read(self, model, **hints):
        """
        Attempts to read Main DB models from 'default'.
        For Account-specific models, it routes to the correct database.
        """
        app_label = model._meta.app_label
        model_name = model._meta.model_name
        
        # 1. Check if it's a model that belongs to the MAIN DB
        if model_name in MAIN_DB_MODELS or app_label == 'auth' or app_label == 'contenttypes':
            return MAIN_DB_LABEL
        
        # 2. Check for an explicit database connection in the query context
        # This is how we pass the per-account database name in Django.
        if hints.get('account_db_name'):
            return hints['account_db_name']

        # Fallback: All other operations default to the main database
        return MAIN_DB_LABEL 

    def db_for_write(self, model, **hints):
        """
        Attempts to write Main DB models to 'default'.
        For Account-specific models, it routes to the correct database.
        """
        # Follows same logic as db_for_read
        return self.db_for_read(model, **hints)

    def allow_relation(self, obj1, obj2, **hints):
        """
        Allow relations between models if they are in the same database.
        This rule simplifies the routing for relations within the same context.
        """
        # Allow relations between any two objects if they are both in the main DB.
        if obj1._state.db == MAIN_DB_LABEL and obj2._state.db == MAIN_DB_LABEL:
            return True
        
        # Disallow relations between Main DB (default) and any Account DB.
        # This enforces isolation.
        if obj1._state.db != obj2._state.db:
            return False
        
        # Allow relations if they are in the same (non-default/account) database.
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        """
        Ensure models migrate to the correct database.
        """
        # Only migrate MAIN_DB_MODELS to the 'default' DB
        if model_name in MAIN_DB_MODELS or app_label == 'auth' or app_label == 'contenttypes':
            return db == MAIN_DB_LABEL
        
        # Do not allow Django to run migrations on dynamically created account DBs.
        # These schemas must be created externally (e.g., via a provisioning script).
        # We assume 'files' app models live in the account DBs.
        if app_label == 'files':
            return db != MAIN_DB_LABEL
            
        return None