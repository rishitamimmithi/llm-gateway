"""Consistent hash ring (app/core/consistent_hash.py)."""
from collections import Counter

import pytest

from app.core.consistent_hash import HashRing

KEYS = [f"key-{i}" for i in range(10_000)]


def owners(ring: HashRing) -> dict[str, str]:
    return {key: ring.get_node(key) for key in KEYS}


def test_empty_ring_raises():
    with pytest.raises(LookupError):
        HashRing().get_node("x")


def test_single_node_owns_everything():
    ring = HashRing(["a"])
    assert {ring.get_node(k) for k in KEYS[:100]} == {"a"}


def test_same_key_same_node_and_same_ring_everywhere():
    first, second = HashRing(["a", "b", "c"]), HashRing(["c", "a", "b"])  # order added doesn't matter
    assert owners(first) == owners(second)


def test_keys_spread_roughly_evenly():
    counts = Counter(owners(HashRing(["a", "b", "c", "d"])).values())
    assert set(counts) == {"a", "b", "c", "d"}
    for count in counts.values():
        assert 0.15 < count / len(KEYS) < 0.35  # ideal 0.25


def test_adding_a_node_moves_about_one_share_and_only_to_the_new_node():
    ring = HashRing(["a", "b", "c"])
    before = owners(ring)
    ring.add_node("d")
    after = owners(ring)
    moved = [k for k in KEYS if before[k] != after[k]]
    assert {after[k] for k in moved} == {"d"}  # nothing shuffles between old nodes
    assert 0.15 < len(moved) / len(KEYS) < 0.35  # ideal 1/4 (vs ~3/4 with hash % N)


def test_removing_a_node_moves_only_its_keys():
    ring = HashRing(["a", "b", "c", "d"])
    before = owners(ring)
    ring.remove_node("b")
    after = owners(ring)
    for key in KEYS:
        if before[key] != "b":
            assert after[key] == before[key]
        else:
            assert after[key] != "b"


def test_add_then_remove_restores_the_original_mapping():
    ring = HashRing(["a", "b", "c"])
    before = owners(ring)
    ring.add_node("d")
    ring.remove_node("d")
    assert owners(ring) == before


def test_duplicate_and_unknown_nodes_are_rejected():
    ring = HashRing(["a"])
    with pytest.raises(ValueError):
        ring.add_node("a")
    with pytest.raises(KeyError):
        ring.remove_node("zzz")
    with pytest.raises(ValueError):
        HashRing(virtual_nodes=0)


def test_more_virtual_nodes_spread_more_evenly():
    def worst_share(virtual_nodes: int) -> float:
        counts = Counter(owners(HashRing(["a", "b", "c", "d"], virtual_nodes=virtual_nodes)).values())
        return max(counts.values()) / len(KEYS)

    assert worst_share(200) < worst_share(1)
