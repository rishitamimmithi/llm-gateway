"""
Sharded exact cache: spread cache entries over several cache nodes (e.g. several Redis servers)
with a consistent hash ring, so each node holds only part of the data and adding a node moves
only about 1/N of the keys to it.

It has the same get/set interface as one ExactCache, so /chat doesn't know it's sharded.
"""
from app.cache.answer import CachedAnswer
from app.cache.exact import ExactCache, cache_key
from app.core.consistent_hash import HashRing


class ShardedExactCache:
    def __init__(self, nodes: dict[str, ExactCache], *, virtual_nodes: int = 100) -> None:
        if not nodes:
            raise ValueError("A sharded cache needs at least one node")
        self.nodes = dict(nodes)
        self.ring = HashRing(self.nodes, virtual_nodes=virtual_nodes)

    def node_for(self, prompt: str) -> str:
        return self.ring.get_node(cache_key(prompt))

    async def get(self, prompt: str) -> CachedAnswer | None:
        return await self.nodes[self.node_for(prompt)].get(prompt)

    async def set(self, prompt: str, answer: CachedAnswer) -> None:
        await self.nodes[self.node_for(prompt)].set(prompt, answer)

    def add_node(self, name: str, cache: ExactCache) -> None:
        """Keys that now map to the new node just miss once and get refilled there."""
        self.ring.add_node(name)
        self.nodes[name] = cache

    async def remove_node(self, name: str) -> None:
        self.ring.remove_node(name)
        await self.nodes.pop(name).aclose()

    async def aclose(self) -> None:
        for cache in self.nodes.values():
            await cache.aclose()
