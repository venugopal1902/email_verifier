import redis
import os
import time
from .consistent_hash import ConsistentHash 

# Initialize Redis connection parameters from .env
REDIS_HOST = os.getenv('REDIS_HOST', 'redis')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))

# --- Consistent Hashing Configuration ---
# Simulating sharding using different Redis logical Databases on the same host.
REDIS_NODES_CONFIG = {
    'shard_01': {'host': REDIS_HOST, 'port': REDIS_PORT, 'db': 2},
    'shard_02': {'host': REDIS_HOST, 'port': REDIS_PORT, 'db': 3},
    'shard_03': {'host': REDIS_HOST, 'port': REDIS_PORT, 'db': 4},
}

# Create the Ring
REDIS_SHARDS = list(REDIS_NODES_CONFIG.keys())
SHARD_RING = ConsistentHash(REDIS_SHARDS)

# --- Connection Pools ---
_CONNECTION_POOLS = {}

def get_redis_connection(node_name=None):
    """Returns a Redis connection for the specific node."""
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
                host=config['host'],
                port=config['port'],
                db=config['db'],
                decode_responses=True
            )
        except Exception as e:
            print(f"ERROR: Failed to create pool for {pool_key}: {e}")
            return None

    return redis.Redis(connection_pool=_CONNECTION_POOLS[pool_key])

# --- Global Key Management ---

def add_to_list(email, list_type='BOUNCE', user_id='system'):
    """
    Adds an email to the GLOBAL distributed list.
    Shards by EMAIL to ensure even distribution across nodes.
    """
    email = email.lower().strip()
    
    # 1. Determine Global Key Name (Same key name exists on all shards)
    key = "GLOBAL:BOUNCE" if list_type == 'BOUNCE' else "GLOBAL:UNSUB"
    
    # 2. Determine Shard based on EMAIL (Content-based sharding)
    target_node = SHARD_RING.get_node(email)
    
    # 3. Connect to that specific shard
    r = get_redis_connection(target_node)
    if not r: return 0

    # 4. Store Email -> UserID map.
    # This deduplicates automatically (Hash structure) while tracking the source.
    return r.hset(key, email, str(user_id))

def check_list(email, list_type='BOUNCE'):
    """
    Checks if an email exists in the global list.
    """
    email = email.lower().strip()
    key = "GLOBAL:BOUNCE" if list_type == 'BOUNCE' else "GLOBAL:UNSUB"
    
    # Find the shard where this email WOULD live
    target_node = SHARD_RING.get_node(email)
    
    r = get_redis_connection(target_node)
    if not r: return False
        
    return r.hexists(key, email)

def delete_from_list(email, list_type='UNSUB'):
    """Deletes an email from the global list."""
    email = email.lower().strip()
    key = "GLOBAL:BOUNCE" if list_type == 'BOUNCE' else "GLOBAL:UNSUB"
    
    target_node = SHARD_RING.get_node(email)
    
    r = get_redis_connection(target_node)
    if not r: return 0

    return r.hdel(key, email)