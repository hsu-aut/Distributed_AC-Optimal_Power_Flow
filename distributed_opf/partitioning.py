"""Connected, impedance-aware partitioning of the bus graph for distributed AC-OPF.

Consensus on a cut branch couples power flow through its series admittance |y|:
a small V/theta disagreement on a stiff branch causes a large balance error.
The partitioner therefore

1. never cuts a leaf bus from its only neighbor and keeps the stiffest branches
   internal (coarsening into supernodes, size-capped),
2. grows k connected regions from spread-out seeds, always adding the adjacent
   node with the strongest admittance connection,
3. balances sizes into a ±20 % band, then moves boundary supernodes when this
   lowers the summed |y| of cut branches, keeping regions connected and in the band.

The construction is repeated from every start bus and three coarsening thresholds;
the result with the smallest size-band violation, then smallest cut admittance, is
returned. Deterministic for a given topology and k. Parallel branches add their admittances.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict, deque
from typing import Dict, Iterable, List, Set, Tuple


def branch_admittance(line) -> float:
    return 1.0 / abs(complex(line.r_pu, line.x_pu))


def pair_weights(topology, buses: Iterable[int] | None = None) -> Dict[Tuple[int, int], float]:
    allowed = set(topology.buses if buses is None else buses)
    weights: Dict[Tuple[int, int], float] = defaultdict(float)
    for line in topology.lines:
        a, b = sorted((line.from_bus, line.to_bus))
        if a != b and a in allowed and b in allowed:
            weights[(a, b)] += branch_admittance(line)
    return dict(weights)


def _connected(nodes: Set, adjacency: Dict) -> bool:
    if not nodes:
        return True
    start = min(nodes)
    seen, queue = {start}, deque([start])
    while queue:
        node = queue.popleft()
        for other in adjacency[node]:
            if other in nodes and other not in seen:
                seen.add(other)
                queue.append(other)
    return len(seen) == len(nodes)


def _size_band(n: int, k: int, balance: float) -> Tuple[int, int]:
    return max(1, math.floor(n / k * (1.0 - balance))), math.ceil(n / k * (1.0 + balance))


def partition_connected_impedance(bus_ids: Iterable[int], topology, num_agents: int,
                                  balance: float = 0.2,
                                  strong_quantiles: Tuple[float, ...] = (0.9, 0.95, 0.8)) -> Dict[int, List[int]]:
    """Best of the deterministic constructions over all start buses and coarsening thresholds.

    Selection key: total size-band violation, then summed cut admittance, then bus lists.
    """
    buses = sorted(int(b) for b in bus_ids)
    n, k = len(buses), max(1, int(num_agents))
    if k == 1:
        return {0: buses}
    if k >= n:
        return {rank: [bus] for rank, bus in enumerate(buses)}
    min_size, max_size = _size_band(n, k, balance)
    weights = pair_weights(topology, buses)
    best = None
    for quantile in strong_quantiles:
        for start in buses:
            try:
                assignment = _partition_once(buses, topology, k, quantile, balance, start)
            except RuntimeError:
                continue
            owner = {b: r for r, bs in assignment.items() for b in bs}
            violation = sum(max(0, len(bs) - max_size) + max(0, min_size - len(bs)) for bs in assignment.values())
            cut = sum(w for (a, b), w in weights.items() if owner[a] != owner[b])
            key = (violation, round(cut, 9), [assignment[r] for r in sorted(assignment)])
            if best is None or key < best[0]:
                best = (key, assignment)
    if best is None:
        raise RuntimeError("No connected partition found")
    return best[1]


def _partition_once(buses: List[int], topology, k: int, strong_quantile: float, balance: float,
                    start_bus: int) -> Dict[int, List[int]]:
    n = len(buses)
    weights = pair_weights(topology, buses)
    adjacency: Dict[int, Dict[int, float]] = {b: {} for b in buses}
    for (a, b), weight in weights.items():
        adjacency[a][b] = weight
        adjacency[b][a] = weight
    min_size, max_size = _size_band(n, k, balance)
    supernode_cap = max(2, math.ceil(n / k / 2))

    # 1) Coarsening with union-find.
    parent = {b: b for b in buses}
    size = {b: 1 for b in buses}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b, cap):
        ra, rb = find(a), find(b)
        if ra == rb or size[ra] + size[rb] > cap:
            return
        if ra > rb:
            ra, rb = rb, ra
        parent[rb] = ra
        size[ra] += size.pop(rb)

    for bus in buses:
        if len(adjacency[bus]) == 1:
            union(bus, next(iter(adjacency[bus])), max_size)
    ordered = sorted(weights.items(), key=lambda item: (-item[1], item[0]))
    threshold = sorted(weights.values())[int(strong_quantile * (len(weights) - 1))] if weights else math.inf
    for (a, b), weight in ordered:
        if weight < threshold:
            break
        union(a, b, supernode_cap)

    members: Dict[int, List[int]] = defaultdict(list)
    for bus in buses:
        members[find(bus)].append(bus)
    node_size = {node: len(bs) for node, bs in members.items()}
    coarse: Dict[int, Dict[int, float]] = {node: defaultdict(float) for node in members}
    for (a, b), weight in weights.items():
        ra, rb = find(a), find(b)
        if ra != rb:
            coarse[ra][rb] += weight
            coarse[rb][ra] += weight

    # 2) Seeds by farthest-first hop distance, then connected growth.
    def hops(source):
        dist, queue = {source: 0}, deque([source])
        while queue:
            node = queue.popleft()
            for other in sorted(coarse[node]):
                if other not in dist:
                    dist[other] = dist[node] + 1
                    queue.append(other)
        return dist

    nodes = sorted(members)
    distance_cache = {}
    first = max(hops(find(start_bus)).items(), key=lambda item: (item[1], -item[0]))[0]
    seeds = [first]
    distance_cache[first] = hops(first)
    while len(seeds) < k:
        candidates = [node for node in nodes if node not in seeds]
        best = max(candidates, key=lambda node: (min(distance_cache[s].get(node, math.inf) for s in seeds), -node))
        seeds.append(best)
        distance_cache[best] = hops(best)
    part_of = {seed: rank for rank, seed in enumerate(seeds)}
    part_size = [node_size[seed] for seed in seeds]

    def connection(node, rank):
        return sum(weight for other, weight in coarse[node].items() if part_of.get(other) == rank)

    while len(part_of) < len(nodes):
        chosen = None
        for rank in sorted(range(k), key=lambda r: (part_size[r], r)):
            frontier = {other for node, r in part_of.items() if r == rank for other in coarse[node] if other not in part_of}
            fitting = [node for node in frontier if part_size[rank] + node_size[node] <= max_size]
            if fitting:
                chosen = (rank, max(fitting, key=lambda node: (connection(node, rank), -node)))
                break
        if chosen is None:
            # No region can grow within the size cap: take the strongest attachment regardless of size.
            pairs = []
            for node in nodes:
                if node in part_of:
                    continue
                for other in coarse[node]:
                    if other in part_of:
                        rank = part_of[other]
                        pairs.append((connection(node, rank), -part_size[rank], -node, rank, node))
            *_, rank, node = max(pairs)
            chosen = (rank, node)
        rank, node = chosen
        part_of[node] = rank
        part_size[rank] += node_size[node]

    def movable(node, source):
        remaining = {other for other, rank in part_of.items() if rank == source and other != node}
        return bool(remaining) and _connected(remaining, coarse)

    def move(node, target):
        source = part_of[node]
        part_of[node] = target
        part_size[source] -= node_size[node]
        part_size[target] += node_size[node]

    # 3a) Balance: bring every region into [min_size, max_size] with the smallest cut increase.
    for _ in range(20 * len(nodes)):
        violating = [r for r in range(k) if part_size[r] > max_size or part_size[r] < min_size]
        if not violating:
            break
        best = None
        for node in nodes:
            source = part_of[node]
            for target in sorted({part_of[other] for other in coarse[node]} - {source}):
                if source not in violating and target not in violating:
                    continue
                new_source, new_target = part_size[source] - node_size[node], part_size[target] + node_size[node]
                # Squared deviation from the mean size must drop; this allows chains of moves
                # that pass a deficit through regions already inside the band.
                mean = n / k
                before = (part_size[source] - mean) ** 2 + (part_size[target] - mean) ** 2
                after = (new_source - mean) ** 2 + (new_target - mean) ** 2
                if after >= before - 1e-9:
                    continue
                increase = connection(node, source) - connection(node, target)
                key = (after - before, increase, node, target)
                if (best is None or key < best) and movable(node, source):
                    best = key
        if best is None:
            break
        move(best[2], best[3])

    # 3b) Refinement: strictly decreasing cut admittance, connectivity and size band preserved.
    for _ in range(50):
        improved = False
        for node in nodes:
            source = part_of[node]
            if part_size[source] - node_size[node] < min_size:
                continue
            current = connection(node, source)
            options = sorted({part_of[other] for other in coarse[node]} - {source})
            best = None
            for target in options:
                gain = connection(node, target) - current
                if gain > 1e-9 and part_size[target] + node_size[node] <= max_size:
                    if best is None or gain > best[0]:
                        best = (gain, target)
            if best is None:
                continue
            remaining = {other for other, rank in part_of.items() if rank == source and other != node}
            if not _connected(remaining, coarse):
                continue
            part_of[node] = best[1]
            part_size[source] -= node_size[node]
            part_size[best[1]] += node_size[node]
            improved = True
        if not improved:
            break

    regions: Dict[int, List[int]] = defaultdict(list)
    for node, rank in part_of.items():
        regions[rank].extend(members[node])
    ordered_regions = sorted((sorted(bs) for bs in regions.values()), key=lambda bs: bs[0])
    assignment = {rank: bs for rank, bs in enumerate(ordered_regions)}
    bus_adjacency = {b: set(adjacency[b]) for b in buses}
    for rank, bs in assignment.items():
        if not _connected(set(bs), bus_adjacency):
            raise RuntimeError(f"Partition {rank} is not connected")
    return assignment


def partition_statistics(assignment: Dict[int, List[int]], topology) -> Dict[str, object]:
    bus_to_rank = {int(b): int(r) for r, bs in assignment.items() for b in bs}
    degree = Counter()
    for line in topology.lines:
        degree[line.from_bus] += 1
        degree[line.to_bus] += 1
    internal: Dict[int, Dict[int, Set[int]]] = defaultdict(lambda: defaultdict(set))
    cut = []
    for index, line in enumerate(topology.lines):
        a, b = line.from_bus, line.to_bus
        if bus_to_rank[a] == bus_to_rank[b]:
            internal[bus_to_rank[a]][a].add(b)
            internal[bus_to_rank[a]][b].add(a)
        else:
            cut.append((branch_admittance(line), index, a, b))
    components = {}
    for rank, bs in assignment.items():
        remaining, count = set(bs), 0
        while remaining:
            count += 1
            queue = deque([remaining.pop()])
            while queue:
                node = queue.popleft()
                for other in internal[rank][node]:
                    if other in remaining:
                        remaining.remove(other)
                        queue.append(other)
        components[int(rank)] = count
    cut.sort(reverse=True)
    slack = [b for b, bus in topology.buses.items() if bus.is_slack]
    return {
        "agents": len(assignment),
        "sizes": [len(assignment[r]) for r in sorted(assignment)],
        "components_per_partition": components,
        "disconnected_partitions": sum(1 for c in components.values() if c > 1),
        "cut_branches": len(cut),
        "cut_bus_pairs": len({tuple(sorted((a, b))) for _, _, a, b in cut}),
        "max_cut_admittance": cut[0][0] if cut else 0.0,
        "sum_cut_admittance": sum(c[0] for c in cut),
        "leaf_bus_cuts": sum(1 for _, _, a, b in cut if degree[a] == 1 or degree[b] == 1),
        "strongest_cut_branches": [{"branch": i, "from": a, "to": b, "admittance": y} for y, i, a, b in cut[:5]],
        "slack_rank": [bus_to_rank[b] for b in slack],
    }


# ---------------------------------------------------------------------------
# Previous partitioner (breadth-first fill), kept unchanged so earlier runs stay reproducible.
# Moved from deploy/generate_partition_config.py.
# ---------------------------------------------------------------------------

def _build_neighbor_map(bus_ids: Iterable[int], topology) -> Dict[int, List[int]]:
    bus_set = set(bus_ids)
    neighbor_map: Dict[int, List[int]] = {}
    for bus_id in bus_set:
        cfg = topology.buses.get(bus_id)
        if cfg is None:
            raise ValueError(f"Bus {bus_id} ist nicht in der Topologie hinterlegt.")
        neighbors = [nbr for nbr in cfg.neighbors if nbr in bus_set]
        neighbor_map[bus_id] = sorted(neighbors)
    return neighbor_map


def partition_graph_aware(bus_ids: Iterable[int], topology, num_agents: int) -> Dict[int, List[int]]:
    assignments: Dict[int, List[int]] = {rank: [] for rank in range(num_agents)}
    bus_ids_sorted = sorted(bus_ids)
    unassigned = set(bus_ids_sorted)
    neighbor_map = _build_neighbor_map(bus_ids_sorted, topology)

    for rank in range(num_agents):
        if not unassigned:
            break
        remaining_agents = num_agents - rank
        target_size = math.ceil(len(unassigned) / remaining_agents) if remaining_agents > 0 else len(unassigned)

        start_bus = min(unassigned)
        queue: deque[int] = deque([start_bus])
        visited_rank = set()

        while queue and len(assignments[rank]) < target_size:
            bus = queue.popleft()
            if bus in visited_rank:
                continue
            visited_rank.add(bus)
            if bus not in unassigned:
                continue
            assignments[rank].append(bus)
            unassigned.remove(bus)
            for neighbor in neighbor_map.get(bus, []):
                if neighbor in unassigned:
                    queue.append(neighbor)

        while len(assignments[rank]) < target_size and unassigned:
            bus = min(unassigned)
            assignments[rank].append(bus)
            unassigned.remove(bus)

    if unassigned:
        for bus in sorted(unassigned):
            for rank in range(num_agents):
                assignments[rank].append(bus)
                break
        unassigned.clear()

    return assignments


def compute_neighbor_ranks(assignments: Dict[int, List[int]], topology) -> Dict[int, List[int]]:
    bus_to_rank: Dict[int, int] = {}
    for rank, buses in assignments.items():
        for bus in buses:
            bus_to_rank[bus] = rank

    rank_neighbors: Dict[int, List[int]] = {}
    for rank, buses in assignments.items():
        neighbor_ranks = set()
        for bus in buses:
            cfg = topology.buses.get(bus)
            if cfg is None:
                raise ValueError(f"Bus {bus} hat keine Topologie-Konfiguration.")
            for neighbor_bus in cfg.neighbors:
                neighbor_rank = bus_to_rank.get(neighbor_bus)
                if neighbor_rank is not None and neighbor_rank != rank:
                    neighbor_ranks.add(neighbor_rank)
        rank_neighbors[rank] = sorted(neighbor_ranks)

    return rank_neighbors
