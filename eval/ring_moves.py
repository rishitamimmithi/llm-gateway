"""
How many cache keys move to a different node when a node is added or removed:
consistent hash ring vs plain `hash % N`.

Run: python -m eval.ring_moves
"""
import argparse
from collections import Counter

from app.core.consistent_hash import HashRing, ring_position


def modulo_owner(key: str, nodes: list[str]) -> str:
    return nodes[ring_position(key) % len(nodes)]


def moved_share(before: dict[str, str], after: dict[str, str]) -> float:
    return sum(before[k] != after[k] for k in before) / len(before)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keys", type=int, default=100_000)
    parser.add_argument("--virtual-nodes", type=int, default=100)
    args = parser.parse_args()
    keys = [f"prompt-{i}" for i in range(args.keys)]

    print(f"{args.keys} keys, {args.virtual_nodes} virtual nodes per node\n")
    print("| change | ideal | consistent hashing | hash % N |")
    print("|---|---|---|---|")
    for old_n, new_n in [(3, 4), (4, 5), (4, 3), (10, 11)]:
        old_nodes = [f"node{i}" for i in range(old_n)]
        new_nodes = [f"node{i}" for i in range(new_n)]
        old_ring = HashRing(old_nodes, virtual_nodes=args.virtual_nodes)
        new_ring = HashRing(new_nodes, virtual_nodes=args.virtual_nodes)
        ring_moved = moved_share({k: old_ring.get_node(k) for k in keys}, {k: new_ring.get_node(k) for k in keys})
        mod_moved = moved_share(
            {k: modulo_owner(k, old_nodes) for k in keys}, {k: modulo_owner(k, new_nodes) for k in keys}
        )
        ideal = abs(new_n - old_n) / max(old_n, new_n)
        print(f"| {old_n} -> {new_n} nodes | {ideal:.0%} | {ring_moved:.1%} | {mod_moved:.1%} |")

    print("\nLoad per node with 4 nodes (share of keys):")
    print("| virtual nodes | lowest | highest |")
    print("|---|---|---|")
    for virtual_nodes in (1, 10, 100, 500):
        ring = HashRing([f"node{i}" for i in range(4)], virtual_nodes=virtual_nodes)
        counts = Counter(ring.get_node(k) for k in keys)
        shares = [counts.get(f"node{i}", 0) / len(keys) for i in range(4)]
        print(f"| {virtual_nodes} | {min(shares):.1%} | {max(shares):.1%} |")


if __name__ == "__main__":
    main()
