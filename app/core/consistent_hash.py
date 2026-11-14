"""
Consistent hashing: decide which cache node owns a key, so that adding or removing a node
only moves a small share of keys (about 1/N), not almost all of them like `hash(key) % N`.

Every node is placed on a ring (the numbers 0 .. 2^64-1) many times, at "virtual node"
positions hash("node#0"), hash("node#1"), ... A key belongs to the first node position at or
after hash(key), wrapping round to the start. More virtual nodes = a more even spread.

Python's built-in hash() is randomised per process, so MD5 is used instead: every gateway
instance must agree on where a key lives. (MD5 is fine here: it's for spreading, not security.)
"""
import bisect
import hashlib
from collections.abc import Iterable


def ring_position(text: str) -> int:
    """A stable 64-bit position on the ring."""
    return int.from_bytes(hashlib.md5(text.encode("utf-8")).digest()[:8], "big")


class HashRing:
    def __init__(self, nodes: Iterable[str] = (), *, virtual_nodes: int = 100) -> None:
        if virtual_nodes < 1:
            raise ValueError("virtual_nodes must be at least 1")
        self.virtual_nodes = virtual_nodes
        self._ring: list[tuple[int, str]] = []  # (position, node), sorted by position
        self._positions: list[int] = []  # just the positions, for bisect
        self._nodes: set[str] = set()
        for node in nodes:
            self.add_node(node)

    @property
    def nodes(self) -> set[str]:
        return set(self._nodes)

    def __len__(self) -> int:
        return len(self._nodes)

    def add_node(self, node: str) -> None:
        if node in self._nodes:
            raise ValueError(f"Node {node!r} is already on the ring")
        self._nodes.add(node)
        self._ring += [(ring_position(f"{node}#{i}"), node) for i in range(self.virtual_nodes)]
        self._rebuild()

    def remove_node(self, node: str) -> None:
        if node not in self._nodes:
            raise KeyError(node)
        self._nodes.remove(node)
        self._ring = [entry for entry in self._ring if entry[1] != node]
        self._rebuild()

    def _rebuild(self) -> None:
        # Sorting (position, node) pairs breaks the (practically impossible) tie of two equal
        # positions by node name, so every gateway instance builds exactly the same ring.
        self._ring.sort()
        self._positions = [position for position, _ in self._ring]

    def get_node(self, key: str) -> str:
        if not self._positions:
            raise LookupError("The ring has no nodes")
        index = bisect.bisect_left(self._positions, ring_position(key))
        if index == len(self._positions):  # past the last position: wrap round to the first
            index = 0
        return self._ring[index][1]
