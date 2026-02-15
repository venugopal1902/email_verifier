import redis
import os
import hashlib

# Initialize Redis connection parameters from .env
REDIS_HOST = os.getenv('REDIS_HOST', 'redis')
REDIS_PORT = int(os.getenv('REDIS_SERVICE_PORT', 6379))

# --- Sharding Configuration ---
REDIS_NODES_CONFIG = {
    'shard_01': {'host': REDIS_HOST, 'port': REDIS_PORT, 'db': 2},
    'shard_02': {'host': REDIS_HOST, 'port': REDIS_PORT, 'db': 3},
    'shard_03': {'host': REDIS_HOST, 'port': REDIS_PORT, 'db': 4},
}

# Sort list to guarantee identical order across all containers
REDIS_SHARDS = sorted(list(REDIS_NODES_CONFIG.keys()))

_CONNECTION_POOLS = {}

def get_redis_connection(node_name=None):
    if not node_name:
        pool_key = 'default'
        config = {'host': REDIS_HOST, 'port': REDIS_PORT, 'db': 0}
    else:
        pool_key = node_name
        config = REDIS_NODES_CONFIG.get(node_name)
        if not config: return None

    if pool_key not in _CONNECTION_POOLS:
        try:
            _CONNECTION_POOLS[pool_key] = redis.ConnectionPool(
                host=config['host'], port=config['port'], db=config['db'], decode_responses=True
            )
        except Exception as e:
            print(f"ERROR: Failed to create pool for {pool_key}: {e}")
            return None

    return redis.Redis(connection_pool=_CONNECTION_POOLS[pool_key])

# --- CRITICAL FIX: DETERMINISTIC HASHING ---
def get_deterministic_node(email):
    """
    Guarantees that 'web' and 'celery' will ALWAYS pick the exact same shard 
    for the exact same email, regardless of the Python process.
    """
    hex_hash = hashlib.md5(email.encode('utf-8')).hexdigest()
    shard_index = int(hex_hash, 16) % len(REDIS_SHARDS)
    return REDIS_SHARDS[shard_index]

# --- Global Key Management ---

def add_to_list(email, list_type='BOUNCE', user_id='system'):
    email = email.lower().strip()
    key = "GLOBAL:BOUNCE" if list_type == 'BOUNCE' else "GLOBAL:UNSUB"
    
    target_node = get_deterministic_node(email)
    r = get_redis_connection(target_node)
    if not r: return 0
    return r.hset(key, email, str(user_id))

def check_list(email, list_type='BOUNCE'):
    email = email.lower().strip()
    key = "GLOBAL:BOUNCE" if list_type == 'BOUNCE' else "GLOBAL:UNSUB"
    
    target_node = get_deterministic_node(email)
    r = get_redis_connection(target_node)
    if not r: return False
    return r.hexists(key, email)

def delete_from_list(email, list_type='UNSUB'):
    email = email.lower().strip()
    key = "GLOBAL:BOUNCE" if list_type == 'BOUNCE' else "GLOBAL:UNSUB"
    
    target_node = get_deterministic_node(email)
    r = get_redis_connection(target_node)
    if not r: return 0
    return r.hdel(key, email)