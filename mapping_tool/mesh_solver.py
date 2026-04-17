import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any, List, Tuple
import random
from collections import deque
import config

# ----------------------------------------------------------------
# Paths
# ----------------------------------------------------------------
DATA_DIR     = Path(config.DATA_DIR)
SHAPES_FILE  = DATA_DIR / config.SHAPES_FILE
GATEWAY_FILE = DATA_DIR / config.GATEWAY_FILE
MESH_FILE    = DATA_DIR / "mesh.geojson"


# ----------------------------------------------------------------
# Data structures
# ----------------------------------------------------------------

@dataclass
class PointLL:
    lat: float
    lon: float


@dataclass
class Relay:
    id: int
    layer: int
    q: int
    r: int
    x: float
    y: float
    lat: float
    lon: float
    active: bool = True
    neighbors: List[int] = field(default_factory=list)
    covered_points: set = field(default_factory=set)


@dataclass
class LinkIdx:
    a: int
    b: int


# ----------------------------------------------------------------
# IO helpers
# ----------------------------------------------------------------

def _load_geojson(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"type": "FeatureCollection", "features": []}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_gateway() -> PointLL | None:
    if not GATEWAY_FILE.exists():
        return None
    with GATEWAY_FILE.open("r", encoding="utf-8") as f:
        feat = json.load(f)
    lng, lat = feat["geometry"]["coordinates"]
    return PointLL(lat=lat, lon=lng)


def _save_mesh(mesh: Dict[str, Any]) -> None:
    MESH_FILE.parent.mkdir(exist_ok=True)
    with MESH_FILE.open("w", encoding="utf-8") as f:
        json.dump(mesh, f, indent=2, ensure_ascii=False)


def clear_mesh_file() -> None:
    if MESH_FILE.exists():
        MESH_FILE.unlink()

def _point_in_polygon(
    x: float, y: float,
    polygon_xy: List[Tuple[float, float]]
) -> bool:
    """
    Ray casting algorithm: returns True if (x, y) is inside the polygon
    defined by polygon_xy (list of (x, y) vertices in local km coords).
    """
    n = len(polygon_xy)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon_xy[i]
        xj, yj = polygon_xy[j]
        if ((yi > y) != (yj > y)) and \
                (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _sample_polygon_points(
    ring_coords: List[List[float]],   # [[lon, lat], ...] outer ring from GeoJSON
    spacing_km: float,
    ref_lat: float,
    ref_lon: float,
) -> List[PointLL]:
    """
    Sample a regular grid of points (spacing_km) inside a polygon.
    ring_coords: outer ring from GeoJSON coordinates[0], each entry [lon, lat].
    Only points inside the polygon boundary are returned.
    """
    # Convert ring to local XY
    ring_xy: List[Tuple[float, float]] = [
        latlon_to_xy_km(lat, lon, ref_lat, ref_lon)
        for lon, lat in ring_coords
    ]

    xs = [x for x, y in ring_xy]
    ys = [y for x, y in ring_xy]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    sampled: List[PointLL] = []
    x = min_x
    while x <= max_x + spacing_km:
        y = min_y
        while y <= max_y + spacing_km:
            if _point_in_polygon(x, y, ring_xy):
                lat, lon = xy_km_to_latlon(x, y, ref_lat, ref_lon)
                sampled.append(PointLL(lat=lat, lon=lon))
            y += spacing_km
        x += spacing_km

    return sampled


def _sample_circle_points(
    center_lat: float,
    center_lon: float,
    radius_m: float,
    spacing_km: float,
    ref_lat: float,
    ref_lon: float,
) -> List[PointLL]:
    """
    Sample a regular grid of points inside a circle.
    radius_m: circle radius in metres (as stored in GeoJSON properties).
    """
    radius_km = radius_m / 1000.0
    cx, cy = latlon_to_xy_km(center_lat, center_lon, ref_lat, ref_lon)
    r2 = radius_km ** 2

    sampled: List[PointLL] = []
    x = cx - radius_km
    while x <= cx + radius_km + spacing_km:
        y = cy - radius_km
        while y <= cy + radius_km + spacing_km:
            if (x - cx) ** 2 + (y - cy) ** 2 <= r2:
                lat, lon = xy_km_to_latlon(x, y, ref_lat, ref_lon)
                sampled.append(PointLL(lat=lat, lon=lon))
            y += spacing_km
        x += spacing_km

    return sampled

# ----------------------------------------------------------------
# Simple geo helpers (equirectangular; easy to swap later)
# ----------------------------------------------------------------

def latlon_to_xy_km(
    lat: float, lon: float,
    lat0: float, lon0: float
) -> Tuple[float, float]:
    km_per_deg_lat = 111.0
    km_per_deg_lon = 111.0 * math.cos(math.radians(lat0))
    dx = (lon - lon0) * km_per_deg_lon
    dy = (lat - lat0) * km_per_deg_lat
    return dx, dy


def xy_km_to_latlon(
    x: float, y: float,
    lat0: float, lon0: float
) -> Tuple[float, float]:
    km_per_deg_lat = 111.0
    km_per_deg_lon = 111.0 * math.cos(math.radians(lat0))
    lat = lat0 + y / km_per_deg_lat
    lon = lon0 + x / km_per_deg_lon
    return lat, lon


# ----------------------------------------------------------------
# Hex grid geometry (axial coords)
# ----------------------------------------------------------------

HEX_DIRECTIONS = [
    ( 1,  0), ( 1, -1), ( 0, -1),
    (-1,  0), (-1,  1), ( 0,  1),
]


def axial_to_xy(q: int, r: int, side_km: float) -> Tuple[float, float]:
    """
    Convert axial hex coordinates (q, r) to local (x, y) in km.
    Pointy-top orientation. side_km = hex side length in km.
    """
    x = side_km * math.sqrt(3) * (q + r / 2.0)
    y = side_km * 1.5 * r
    return x, y


def generate_hex_ring(radius: int) -> List[Tuple[int, int]]:
    """
    All axial (q, r) coords exactly 'radius' steps from origin.
    radius 0 → [(0, 0)]
    radius 1 → 6 neighbours
    radius n → 6*n hexes
    """
    if radius == 0:
        return [(0, 0)]

    results: List[Tuple[int, int]] = []
    dq4, dr4 = HEX_DIRECTIONS[4]   # (-1, 1)
    q = dq4 * radius
    r = dr4 * radius

    for i in range(6):
        dq, dr = HEX_DIRECTIONS[i]
        for _ in range(radius):
            results.append((q, r))
            q += dq
            r += dr

    return results


# ----------------------------------------------------------------
# Relay mesh generation
# ----------------------------------------------------------------

def _build_relays(
    num_rings: int,
    relay_range_km: float,
    center_lat: float,
    center_lon: float
) -> List[Relay]:
    """
    Generate relay objects in axial coords up to num_rings,
    with gateway as center (q=0, r=0, layer 0).
    """
    hex_side_km = relay_range_km / math.sqrt(3)
    relays: List[Relay] = []
    id_counter = 0

    for radius in range(0, num_rings + 1):
        for q, r in generate_hex_ring(radius):
            x, y = axial_to_xy(q, r, hex_side_km)
            lat, lon = xy_km_to_latlon(x, y, center_lat, center_lon)
            relays.append(
                Relay(
                    id=id_counter,
                    layer=radius,
                    q=q,
                    r=r,
                    x=x,
                    y=y,
                    lat=lat,
                    lon=lon,
                    active=True,
                )
            )
            id_counter += 1

    # Build neighbor lists based on axial coordinates
    coord_to_id: Dict[Tuple[int, int], int] = {
        (rly.q, rly.r): rly.id for rly in relays
    }
    for rly in relays:
        for dq, dr in HEX_DIRECTIONS:
            nbr_coord = (rly.q + dq, rly.r + dr)
            nbr_id = coord_to_id.get(nbr_coord)
            if nbr_id is not None:
                rly.neighbors.append(nbr_id)

    return relays


def _all_demands_covered(
    demand_xy: List[Tuple[float, float]],
    relays: List[Relay],
    relay_range_km: float,
) -> bool:
    """
    Check if every demand point (in local XY km) is within relay_range_km
    of at least one active relay.
    """
    r2 = relay_range_km ** 2
    for (dx, dy) in demand_xy:
        covered = False
        for rly in relays:
            if not rly.active:
                continue
            ddx = rly.x - dx
            ddy = rly.y - dy
            if ddx*ddx + ddy*ddy <= r2:
                covered = True
                break
        if not covered:
            return False
    return True

def _compute_coverage(
    relays: List[Relay],
    demand_xy: List[Tuple[float, float]],
    relay_range_km: float,
) -> None:
    """
    For each relay, compute which demand point indices (into demand_xy)
    fall within relay_range_km. Stores result in relay.covered_points.
    Called once after mesh generation, before any pruning.
    """
    r2 = relay_range_km ** 2
    for rly in relays:
        rly.covered_points = set()
        for i, (dx, dy) in enumerate(demand_xy):
            ddx = rly.x - dx
            ddy = rly.y - dy
            if ddx * ddx + ddy * ddy <= r2:
                rly.covered_points.add(i)


def _step1_disable_zero_coverage(relays: List[Relay], gw_idx: int) -> int:
    """
    Disable any relay that covers no real demand points.
    Gateway point (gw_idx) is excluded from this check since it is
    always covered by relay 0. Relay 0 itself is always kept active.
    """
    disabled = 0
    for rly in relays:
        if rly.id == 0:
            # Relay 0 is the gateway node, always stays active
            continue
        real_coverage = rly.covered_points - {gw_idx}
        if len(real_coverage) == 0:
            rly.active = False
            disabled += 1
    print(f"[Step 1] Disabled {disabled} relays covering only gateway or nothing "
          f"({len(relays) - disabled} remaining active)")
    return disabled

def _step2_monte_carlo(
    relays: List[Relay],
    demand_xy: List[Tuple[float, float]],
    gw_idx: int,
    iterations: int = 1000,
) -> None:
    """
    Step 2 pruning: Monte Carlo search for the minimal relay set that
    still covers all real demand points (gateway excluded from check).

    Each iteration randomly shuffles active candidates and greedily
    tries to disable each one. A relay can be disabled if every demand
    point it covers is still covered by at least one other active relay.

    Scoring (lower is better):
      Primary:   fewest active relays
      Secondary: lowest sum of layer indexes (prefers inner-ring relays)

    Relay 0 (gateway) is never disabled.
    Modifies relay.active in place to the best configuration found.
    """
    import random

    # Real demand indices: everything except the gateway point
    real_pts: set = set(range(len(demand_xy))) - {gw_idx}

    if not real_pts:
        # No real demand points, only gateway → nothing to optimise
        return

    # Candidates: active relays after step 1, excluding relay 0
    candidates = [r for r in relays if r.active and r.id != 0]

    if not candidates:
        return

    # ------------------------------------------------------------------
    # Baseline score from step 1 result
    # ------------------------------------------------------------------
    baseline_active = frozenset(r.id for r in relays if r.active)
    best_active     = baseline_active
    best_count      = len(best_active)
    best_layer_sum  = sum(relays[rid].layer for rid in best_active)

    print(f"[Step 2] Monte Carlo start → baseline: "
          f"{best_count} relays, layer sum {best_layer_sum}, "
          f"{iterations} iterations")

    # ------------------------------------------------------------------
    # Monte Carlo loop
    # ------------------------------------------------------------------
    for _ in range(iterations):

        # Each iteration starts fresh from the step-1 active set.
        # Track active state and per-point coverage counts locally
        # so we never mutate relay objects mid-iteration.
        local_active: Dict[int, bool] = {
            r.id: r.active for r in relays
        }

        # coverage_count[p] = number of currently active relays covering p
        coverage_count: Dict[int, int] = {p: 0 for p in real_pts}
        for r in relays:
            if local_active[r.id]:
                for p in r.covered_points:
                    if p in real_pts:
                        coverage_count[p] += 1

        # Random evaluation order each iteration
        shuffled = candidates.copy()
        random.shuffle(shuffled)

        for r in shuffled:
            if not local_active[r.id]:
                continue  # already disabled earlier this iteration

            # Can we disable r? Yes if every real point it covers
            # has coverage_count >= 2 (at least one other relay covers it)
            can_disable = all(
                coverage_count.get(p, 0) >= 2
                for p in r.covered_points
                if p in real_pts
            )

            if can_disable:
                local_active[r.id] = False
                for p in r.covered_points:
                    if p in real_pts:
                        coverage_count[p] -= 1

        # ------------------------------------------------------------------
        # Score this iteration
        # ------------------------------------------------------------------
        it_active    = frozenset(rid for rid, act in local_active.items() if act)
        it_count     = len(it_active)
        it_layer_sum = sum(relays[rid].layer for rid in it_active)

        # Compare: primary = relay count, secondary = layer sum
        if (it_count, it_layer_sum) < (best_count, best_layer_sum):
            best_active    = it_active
            best_count     = it_count
            best_layer_sum = it_layer_sum

    # ------------------------------------------------------------------
    # Apply best configuration found
    # ------------------------------------------------------------------
    for r in relays:
        if r.id == 0:
            r.active = True   # gateway always active
            continue
        r.active = r.id in best_active

    improved = best_count < len(baseline_active)
    print(f"[Step 2] Best found: {best_count} relays, "
          f"layer sum {best_layer_sum} "
          f"({'improved' if improved else 'same as step 1'})")

def _coverage_relays(relays: List[Relay], gw_idx: int) -> List[int]:
    """
    Return relay ids that are active after coverage optimization and
    cover at least one real demand point (excluding gateway-only coverage).
    Relay 0 is excluded.
    """
    result = []
    for r in relays:
        if r.id == 0:
            continue
        if not r.active:
            continue
        real_coverage = r.covered_points - {gw_idx}
        if real_coverage:
            result.append(r.id)
    return result

def _bfs_to_component(
    relays: List[Relay],
    start_id: int,
    target_component: set[int],
) -> List[int] | None:
    """
    BFS on the full hex graph to find a shortest path from start_id
    to ANY node in target_component. Returns the list of relay ids
    from start_id to some node in target_component (inclusive),
    or None if no path exists (should not happen in a connected hex grid).
    """
    if start_id in target_component:
        return [start_id]

    q = deque([start_id])
    prev: Dict[int, int | None] = {start_id: None}

    while q:
        cur = q.popleft()
        if cur in target_component:
            # reconstruct path from start_id to cur
            path = []
            node = cur
            while node is not None:
                path.append(node)
                node = prev[node]
            path.reverse()
            return path

        for nbr_id in relays[cur].neighbors:
            if nbr_id not in prev:
                prev[nbr_id] = cur
                q.append(nbr_id)

    return None

def _bfs_path_through_active(
    relays: List[Relay],
    start_id: int,
    active_set: set,
) -> List[int] | None:
    """
    BFS from start_id to gateway (relay 0) using only nodes in active_set.
    Returns list of relay ids start→gateway, or None if no path exists.
    Used to find the established primary path so we know what to block
    when searching for the secondary path.
    """
    if start_id == 0:
        return [0]

    q = deque([start_id])
    prev: Dict[int, int | None] = {start_id: None}

    while q:
        cur = q.popleft()
        if cur == 0:
            path = []
            node: int | None = cur
            while node is not None:
                path.append(node)
                node = prev[node]
            path.reverse()
            return path

        for nbr in relays[cur].neighbors:
            if nbr not in prev and nbr in active_set:
                prev[nbr] = cur
                q.append(nbr)

    return None


def _bfs_to_component_avoiding(
    relays: List[Relay],
    start_id: int,
    target_component: set,
    blocked: set,
) -> List[int] | None:
    """
    BFS on the full hex graph from start_id to any node in target_component,
    while avoiding nodes in blocked (start_id and target nodes are exempt).
    Returns path start→target, or None if no path found.
    Used to find the secondary (backup) path that is node-disjoint from
    the primary path.
    """
    if start_id in target_component:
        return [start_id]

    q = deque([start_id])
    prev: Dict[int, int | None] = {start_id: None}

    while q:
        cur = q.popleft()

        # Reached a node in target component (not the start)
        if cur in target_component and cur != start_id:
            path = []
            node: int | None = cur
            while node is not None:
                path.append(node)
                node = prev[node]
            path.reverse()
            return path

        for nbr in relays[cur].neighbors:
            if nbr not in prev and nbr not in blocked:
                prev[nbr] = cur
                q.append(nbr)

    return None

def _random_walk_to_component(
    relays: List[Relay],
    start_id: int,
    target_component: set,
    max_steps: int = 300,
) -> List[int] | None:
    """
    Random walk from start_id to any node already in target_component.
    Uses layer-biased random choice at each step (strongly prefer
    lower-layer neighbors, allow same-layer, rarely go outward).

    Unlike BFS this finds DIFFERENT paths on different calls, which
    lets the Monte Carlo explore shared-corridor solutions.
    Returns list of relay ids start→target, or None if stuck.
    """
    if start_id in target_component:
        return [start_id]

    path    = [start_id]
    visited = {start_id}
    current = start_id

    for _ in range(max_steps):
        # Reached target component
        if current in target_component and current != start_id:
            return path

        nbrs = [
            nid for nid in relays[current].neighbors
            if nid not in visited
        ]
        if not nbrs:
            return None  # stuck, this iteration is invalid

        current_layer = relays[current].layer

        # Any neighbor already in target component? Take it immediately.
        in_target = [nid for nid in nbrs if nid in target_component]
        if in_target:
            nxt = random.choice(in_target)
            path.append(nxt)
            return path

        # Biased random choice:
        #   strong preference inward (lower layer)
        #   weak allowance for same layer (lateral moves enable L-shapes)
        #   rare outward step (avoids getting trapped)
        inward = [nid for nid in nbrs if relays[nid].layer < current_layer]
        same   = [nid for nid in nbrs if relays[nid].layer == current_layer]
        outer  = [nid for nid in nbrs if relays[nid].layer > current_layer]

        weighted = inward * 6 + same * 2 + outer * 1
        if not weighted:
            return None

        nxt = random.choice(weighted)
        path.append(nxt)
        visited.add(nxt)
        current = nxt

    return None

def _paths_are_disjoint(path1: List[int], path2: List[int]) -> bool:
    """
    Check node-disjointness except for endpoints:
    - start relay may be shared
    - gateway 0 may be shared
    """
    if not path1 or not path2:
        return False

    internal1 = set(path1[1:-1])
    internal2 = set(path2[1:-1])
    return internal1.isdisjoint(internal2)

def _verify_two_disjoint_paths(
    relays: List[Relay],
    coverage_relay_ids: List[int],
    active_set: set,
) -> bool:
    """
    For every coverage relay, confirm there exist 2 node-disjoint paths
    to gateway (relay 0) through active_set.

    Method:
      1. Find primary path R → gateway through active_set.
      2. Remove its internal nodes from active_set.
      3. Find secondary path R → gateway in the reduced active_set.
      4. If both exist for every coverage relay: valid. Else: invalid.

    The coverage relay itself (start) and gateway (end) are allowed to
    be shared between paths. All internal nodes must be disjoint.
    """
    for rid in coverage_relay_ids:
        # Primary path through full active set
        primary = _bfs_path_through_active(relays, rid, active_set)
        if primary is None:
            return False

        # Internal nodes of primary path (exclude start and gateway)
        blocked = set(primary[1:-1])

        # Secondary path through active set minus blocked internal nodes
        reduced = active_set - blocked
        secondary = _bfs_path_through_active(relays, rid, reduced)
        if secondary is None:
            return False

    return True

def _step3_connect_global_monte_carlo(
    relays: List[Relay],
    gw_idx: int,
    iterations: int = 1000,
) -> None:
    """
    Step 3: global Monte Carlo backbone with verified redundancy.

    Each iteration has two phases:

    Phase 1 – Primary global backbone:
      Connect all coverage relays to gateway via random biased walks.
      Each relay connects to the current backbone component C, naturally
      sharing corridors with previously connected relays.

    Phase 2 – Secondary paths:
      For each coverage relay:
        - Find its established primary path through active nodes.
        - Block all internal nodes of that primary path.
        - BFS on the full hex graph to GATEWAY SPECIFICALLY (relay 0),
          avoiding blocked nodes. This guarantees the secondary path
          is node-disjoint from the primary path all the way to gateway.
        - Activate all relays on the secondary path.

    Verification:
      After both phases, confirm every coverage relay genuinely has
      2 node-disjoint paths to gateway in the resulting active graph.
      Only iterations passing verification contribute to the best result.

    Score (lower is better):
      Primary:   total active relay count
      Secondary: sum of layer indexes
    """
    must_connect = _coverage_relays(relays, gw_idx)

    if not must_connect:
        print("[Step 3] No coverage relays to connect")
        relays[0].active = True
        return

    base_active = frozenset(r.id for r in relays if r.active)
    best_active: frozenset | None = None
    best_score: Tuple[int, int] | None = None
    valid_count = 0

    print(f"[Step 3] Global Monte Carlo with verified redundancy: "
          f"{len(must_connect)} coverage relays, {iterations} iterations")

    for _ in range(iterations):
        local_active: set = set(base_active)
        C: set = {0}
        failed = False

        # -----------------------------------------------------------------
        # Phase 1: primary global backbone
        # Random walks toward C encourage shared corridors over straight lines
        # -----------------------------------------------------------------
        order = must_connect[:]
        random.shuffle(order)

        for rid in order:
            if rid in C:
                continue

            # Quick check: already touching backbone?
            if any(nbr in C and nbr in local_active
                   for nbr in relays[rid].neighbors):
                C.add(rid)
                continue

            # Random biased walk to nearest node in C
            path = _random_walk_to_component(relays, rid, C)
            if path is None:
                failed = True
                break

            for nid in path:
                local_active.add(nid)
                C.add(nid)

        if failed:
            continue

        # -----------------------------------------------------------------
        # Phase 2: secondary node-disjoint paths per coverage relay
        # Target is gateway (relay 0) specifically, NOT just any C node.
        # This guarantees the FULL secondary route to gateway is disjoint.
        # -----------------------------------------------------------------
        for rid in must_connect:
            # Find the established primary path through active nodes
            primary = _bfs_path_through_active(relays, rid, local_active)
            if primary is None:
                failed = True
                break

            # Block ALL internal nodes of primary path
            # primary[0] = coverage relay (start, not blocked)
            # primary[-1] = gateway relay 0 (end, not blocked)
            blocked = set(primary[1:-1])

            # BFS on full hex graph to GATEWAY (relay 0) avoiding blocked
            # Using full graph (not just active) to allow activation of
            # new relay nodes for the backup route
            secondary = _bfs_to_component_avoiding(
                relays,
                rid,
                {0},       # target: gateway specifically, not just any C node
                blocked,   # internal nodes of primary path
            )

            if secondary is None:
                failed = True
                break

            # Activate secondary path
            for nid in secondary:
                local_active.add(nid)
                C.add(nid)

        if failed:
            continue

        # -----------------------------------------------------------------
        # Verification: confirm 2 truly node-disjoint paths for all relays
        # This catches any edge case where Phase 2 produced an incomplete
        # or overlapping solution
        # -----------------------------------------------------------------
        if not _verify_two_disjoint_paths(relays, must_connect, local_active):
            continue

        valid_count += 1

        # Score: primary = relay count, secondary = layer sum
        active_count = len(local_active)
        layer_sum    = sum(relays[nid].layer for nid in local_active)
        score        = (active_count, layer_sum)

        if best_score is None or score < best_score:
            best_score  = score
            best_active = frozenset(local_active)

    print(f"[Step 3] Valid iterations: {valid_count}/{iterations}")

    # -----------------------------------------------------------------
    # Apply best configuration
    # -----------------------------------------------------------------
    if best_active is None:
        print("[Step 3] No fully redundant solution found; "
              "keeping step 2 configuration with gateway active")
        relays[0].active = True
        return

    for r in relays:
        r.active = r.id in best_active
    relays[0].active = True

    print(f"[Step 3] Best: {best_score[0]} active relays, "
          f"layer sum {best_score[1]}")

# ----------------------------------------------------------------
# Main entry point
# ----------------------------------------------------------------

def compute_mesh(relay_range_km: float = 10.0) -> Dict[str, Any]:
    """
    Build a hex mesh around all demand points, with the gateway as center.

    relay_range_km: coverage radius per relay (km).
    """
    shapes = _load_geojson(SHAPES_FILE)
    gw_pt  = _load_gateway()

    for feat in shapes.get("features", []):
        geom       = feat.get("geometry", {})
        props      = feat.get("properties", {})
        print(f"[DEBUG] geom_type={geom.get('type')} "
            f"shape_type={props.get('shape_type')} "
            f"radius={props.get('radius')} "
            f"props={props}")

    # ------------------------------------------------------------------
    # 1. Determine center FIRST so shape sampling has a reference point
    # ------------------------------------------------------------------
    if gw_pt is not None:
        center_lat = gw_pt.lat
        center_lon = gw_pt.lon
    else:
        # Fallback: rough center from markers only (no gateway case)
        raw_markers = []
        for f in shapes.get("features", []):
            geom  = f.get("geometry", {})
            props = f.get("properties", {})
            if (geom.get("type") == "Point"
                    and props.get("shape_type") == "marker"):
                lng, lat = geom["coordinates"]
                raw_markers.append(PointLL(lat=lat, lon=lng))
        if not raw_markers:
            return {
                "type": "FeatureCollection",
                "features": [],
                "properties": {"relay_range_m": relay_range_km * 1000.0},
            }
        center_lat = sum(p.lat for p in raw_markers) / len(raw_markers)
        center_lon = sum(p.lon for p in raw_markers) / len(raw_markers)

    # ------------------------------------------------------------------
    # 2. Collect all demand points (markers + sampled shapes)
    #    center_lat/center_lon are now defined before this loop
    # ------------------------------------------------------------------
    spacing_km   = relay_range_km / 2.0
    demand_ll: List[PointLL] = []

    for feat in shapes.get("features", []):
        geom       = feat.get("geometry", {})
        props      = feat.get("properties", {})
        shape_type = props.get("shape_type", "")
        geom_type  = geom.get("type", "")

        if geom_type == "Point" and shape_type == "marker":
            lng, lat = geom["coordinates"]
            demand_ll.append(PointLL(lat=lat, lon=lng))

        elif geom_type == "Polygon" and shape_type in ("polygon", "rectangle"):
            outer_ring = geom["coordinates"][0]
            sampled = _sample_polygon_points(
                outer_ring, spacing_km, center_lat, center_lon
            )
            demand_ll.extend(sampled)
            print(f"[Shapes] {shape_type} → {len(sampled)} sample points")

        elif geom_type == "Point" and shape_type == "circle":
            lng, lat = geom["coordinates"]
            radius_m = props.get("radius", 0.0)
            sampled  = _sample_circle_points(
                lat, lng, radius_m, spacing_km, center_lat, center_lon
            )
            demand_ll.extend(sampled)
            print(f"[Shapes] circle (r={radius_m/1000:.1f} km) "
                  f"→ {len(sampled)} sample points")

    # ------------------------------------------------------------------
    # 3. Add gateway as last demand point and record its index
    # ------------------------------------------------------------------
    if gw_pt is not None:
        demand_ll.append(gw_pt)
        gw_idx = len(demand_ll) - 1
    else:
        gw_idx = -1

    if not demand_ll:
        return {
            "type": "FeatureCollection",
            "features": [],
            "properties": {"relay_range_m": relay_range_km * 1000.0},
        }

    # ------------------------------------------------------------------
    # 4. Convert all demands to local XY (km) around center
    # ------------------------------------------------------------------
    demand_xy: List[Tuple[float, float]] = [
        latlon_to_xy_km(p.lat, p.lon, center_lat, center_lon)
        for p in demand_ll
    ]

    D_max = max(math.hypot(x, y) for (x, y) in demand_xy)

    # ------------------------------------------------------------------
    # 5. Estimate rings and grow until all demands are geometrically covered
    #    Only pure coverage check here — no pruning yet.
    #    Steps 1/2/3 run ONCE after this loop, not inside it.
    # ------------------------------------------------------------------
    if D_max <= relay_range_km:
        num_rings = 0
        print(f"All points within {relay_range_km} km of gateway "
              f"→ single relay at gateway")
    else:
        num_rings = max(0, math.ceil((D_max - relay_range_km) / relay_range_km))
        print(f"D_max={D_max:.2f} km → initial estimate {num_rings} rings")

    while True:
        relays = _build_relays(num_rings, relay_range_km, center_lat, center_lon)
        _compute_coverage(relays, demand_xy, relay_range_km)
        if _all_demands_covered(demand_xy, relays, relay_range_km):
            print(f"Coverage confirmed: {num_rings} rings, "
                  f"{len(relays)} candidate relays")
            break
        num_rings += 1
        print(f"Coverage failed → increasing to {num_rings} rings")

    # ------------------------------------------------------------------
    # 6. Pruning and connectivity (run once on confirmed mesh)
    # ------------------------------------------------------------------
    _step1_disable_zero_coverage(relays, gw_idx)
    _step2_monte_carlo(relays, demand_xy, gw_idx, iterations=1000)
    _step3_connect_global_monte_carlo(relays, gw_idx, iterations=5000)

    # ---------------------------------------------------------
    # ------------------------------------------------------------------
    # 7. Build GeoJSON output
    # ------------------------------------------------------------------
    links: List[LinkIdx] = []
    for rly in relays:
        if not rly.active:
            continue
        for nbr_id in rly.neighbors:
            if nbr_id > rly.id and relays[nbr_id].active:
                links.append(LinkIdx(rly.id, nbr_id))

    features: List[Dict[str, Any]] = []

    for r in relays:
        if not r.active:
            continue
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [r.lon, r.lat],
            },
            "properties": {
                "type": "relay",
                "id": f"R{r.id}",
                "layer": r.layer,
                "active": r.active,
            },
        })

    for j, link in enumerate(links):
        a = relays[link.a]
        b = relays[link.b]
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [a.lon, a.lat],
                    [b.lon, b.lat],
                ],
            },
            "properties": {
                "type": "link",
                "id": f"L{j}",
                "from": f"R{a.id}",
                "to": f"R{b.id}",
            },
        })

    mesh = {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "relay_range_m": relay_range_km * 1000.0,
        },
    }

    _save_mesh(mesh)
    return mesh