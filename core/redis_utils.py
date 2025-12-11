import redis
import os
import time
from .consistent_hash import ConsistentHash  # Use local implementation instead of PyPI package

# Initialize Redis connection parameters from .env
REDIS_HOST = os.getenv('REDIS_HOST', 'redis')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))

# --- Consistent Hashing Setup ---
# Shards represent logical nodes where the lists are stored.
# For simplicity, we use one node here, but the structure allows easy scaling.
REDIS_SHARDS = ['redis_node_1'] 
SHARD_RING = ConsistentHash(REDIS_SHARDS)

# --- Connection Pools ---
# Use DB 2 for Bounce/Unsubscribe list storage (dedicated memory space)
try:
    REDIS_POOL = redis.ConnectionPool(host=REDIS_HOST, port=REDIS_PORT, db=2, decode_responses=True)
    
    def get_redis_connection():
        """Returns a Redis connection from the pool."""
        return redis.Redis(connection_pool=REDIS_POOL)

except Exception as e:
    # Print error but allow app to start for other functions
    print(f"ERROR: Failed to initialize Redis connection pool for lists: {e}")
    get_redis_connection = lambda: None 

# --- Key Management ---
# The Hash function in the ConsistentHash package determines the physical node.
# The logical key is always the same for a given account/list type.

def get_hash_node(key):
    """Returns the logical node (shard) responsible for the given key."""
    return SHARD_RING.get_node(key)

def get_bounce_key(account_id):
    """Generates the main Redis Hash key for the account's bounce list."""
    return f"A{account_id}:BOUNCE"

def get_unsub_key(account_id):
    """Generates the main Redis Hash key for the account's unsubscribe list."""
    return f"A{account_id}:UNSUB"

def add_to_list(account_id, email, list_type='BOUNCE', reason="Manual"):
    """Adds an email to the specified list in Redis using HSET (O(1))."""
    r = get_redis_connection()
    if not r: return 0

    key = get_bounce_key(account_id) if list_type == 'BOUNCE' else get_unsub_key(account_id)
        
    # HSET returns 1 if added, 0 if updated (unique check)
    return r.hset(key, email.lower(), f"{reason}|{time.time()}")

def check_list(account_id, email, list_type='BOUNCE'):
    """Checks if an email exists in the specified list (O(1))."""
    r = get_redis_connection()
    if not r: return False
    
    key = get_bounce_key(account_id) if list_type == 'BOUNCE' else get_unsub_key(account_id)
        
    return r.hexists(key, email.lower())

def delete_from_list(account_id, email, list_type='UNSUB'):
    """Deletes an email from the specified list. Returns 1 if deleted, 0 otherwise."""
    r = get_redis_connection()
    if not r: return 0

    key = get_bounce_key(account_id) if list_type == 'BOUNCE' else get_unsub_key(account_id)
        
    return r.hdel(key, email.lower())