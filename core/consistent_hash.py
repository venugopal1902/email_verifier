"""
Simple local implementation of a consistent hashing ring with virtual nodes.
This replaces the unavailable `consistent-hash` PyPI package used previously.

API:
    CH = ConsistentHash(nodes, replicas=100)
    node = CH.get_node(key)

This is intentionally minimal and sufficient for the project's use in
`core/redis_utils.py` where we only need to map a key to a logical node.
"""
import hashlib
import bisect

class ConsistentHash:
    def __init__(self, nodes=None, replicas=100):
        self.replicas = replicas
        self.ring = []  # sorted list of hashes
        self._nodes = {}  # map hash -> node

        nodes = nodes or []
        for n in nodes:
            self.add_node(n)

    def _hash(self, key: str) -> int:
        # Use md5 to produce a stable 128-bit hash and convert to int
        return int(hashlib.md5(key.encode('utf-8')).hexdigest(), 16)

    def add_node(self, node):
        for i in range(self.replicas):
            replica_key = f"{node}:{i}"
            h = self._hash(replica_key)
            self._nodes[h] = node
            bisect.insort(self.ring, h)

    def remove_node(self, node):
        to_remove = []
        for i in range(self.replicas):
            replica_key = f"{node}:{i}"
            h = self._hash(replica_key)
            to_remove.append(h)
            self._nodes.pop(h, None)

        # Remove from ring
        for h in to_remove:
            idx = bisect.bisect_left(self.ring, h)
            if idx < len(self.ring) and self.ring[idx] == h:
                self.ring.pop(idx)

    def get_node(self, key: str):
        if not self.ring:
            return None
        h = self._hash(key)
        # Find the first node with hash >= h, wrap-around using modulo
        idx = bisect.bisect(self.ring, h) % len(self.ring)
        node_hash = self.ring[idx]
        return self._nodes.get(node_hash)
