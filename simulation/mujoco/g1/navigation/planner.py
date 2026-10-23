"""在膨胀栅格上的增量 D* Lite 与路径简化。"""

from __future__ import annotations

import heapq
import itertools
import math
from typing import Tuple

import numpy as np

from ..contracts import MapContext, NavigationGoal, NavigationPlan, Pose2D
from .map import cell_to_world, iter_free_neighbors, world_to_cell


Cell = Tuple[int, int]
Key = Tuple[float, float]
_EPSILON = 1e-12


def _key_less(first: Key, second: Key) -> bool:
    if first[0] < second[0] - _EPSILON:
        return True
    if first[0] > second[0] + _EPSILON:
        return False
    return first[1] < second[1] - _EPSILON


def _different(first: float, second: float) -> bool:
    return not math.isclose(first, second, rel_tol=0.0, abs_tol=_EPSILON)


def _octile(a: Cell, b: Cell) -> float:
    dy, dx = abs(a[0] - b[0]), abs(a[1] - b[1])
    return min(dx, dy) * math.sqrt(2.0) + abs(dx - dy)


def _line_cells(start: Cell, end: Cell) -> tuple[Cell, ...]:
    dy, dx = end[0] - start[0], end[1] - start[1]
    count = max(abs(dx), abs(dy)) * 2 + 1
    result: list[Cell] = []
    for index in range(count + 1):
        alpha = index / count
        cell = round(start[0] + alpha * dy), round(start[1] + alpha * dx)
        if not result or cell != result[-1]:
            result.append(cell)
    return tuple(result)


def _line_free(
    context: MapContext,
    start: Cell,
    end: Cell,
    minimum_clearance_m: float,
) -> bool:
    cells = _line_cells(start, end)
    previous = cells[0]
    for cell in cells:
        if (
            not context.traversable[cell]
            or float(context.clearance_m[cell]) < minimum_clearance_m
        ):
            return False
        dy, dx = cell[0] - previous[0], cell[1] - previous[1]
        if dy and dx and not (
            context.traversable[previous[0] + dy, previous[1]]
            and context.traversable[previous[0], previous[1] + dx]
        ):
            return False
        previous = cell
    return True


def _simplify(
    context: MapContext,
    cells: list[Cell],
    minimum_clearance_m: float,
) -> list[Cell]:
    if len(cells) <= 2:
        return cells
    result = [cells[0]]
    anchor = 0
    while anchor < len(cells) - 1:
        candidate = len(cells) - 1
        while candidate > anchor + 1 and not _line_free(
            context, cells[anchor], cells[candidate], minimum_clearance_m
        ):
            candidate -= 1
        result.append(cells[candidate])
        anchor = candidate
    return result


class DStarLitePlanner:
    """支持起点移动和占据/净空变化的八邻域 D* Lite。"""

    def __init__(self, clearance_cost_margin_m: float = 0.2) -> None:
        self.clearance_cost_margin_m = clearance_cost_margin_m
        self.last_reused_state = False
        self.last_expanded_nodes = 0
        self._context: MapContext | None = None
        self._start: Cell | None = None
        self._last_start: Cell | None = None
        self._goal: Cell | None = None
        self._km = 0.0
        self._g: np.ndarray | None = None
        self._rhs: np.ndarray | None = None
        self._queue: list[tuple[float, float, int, Cell]] = []
        self._queued_keys: dict[Cell, Key] = {}
        self._counter = itertools.count()
        self._traversable_snapshot: np.ndarray | None = None
        self._clearance_snapshot: np.ndarray | None = None

    def _heuristic(self, first: Cell, second: Cell) -> float:
        assert self._context is not None
        return _octile(first, second) * self._context.resolution_m

    def _calculate_key(self, cell: Cell) -> Key:
        assert self._g is not None and self._rhs is not None and self._start is not None
        value = min(float(self._g[cell]), float(self._rhs[cell]))
        return value + self._heuristic(self._start, cell) + self._km, value

    def _push(self, cell: Cell) -> None:
        key = self._calculate_key(cell)
        self._queued_keys[cell] = key
        heapq.heappush(self._queue, (*key, next(self._counter), cell))

    def _remove(self, cell: Cell) -> None:
        self._queued_keys.pop(cell, None)

    def _top_key(self) -> Key:
        while self._queue:
            first, second, _, cell = self._queue[0]
            if self._queued_keys.get(cell) == (first, second):
                return first, second
            heapq.heappop(self._queue)
        return math.inf, math.inf

    def _pop(self) -> tuple[Key, Cell]:
        while self._queue:
            first, second, _, cell = heapq.heappop(self._queue)
            key = first, second
            if self._queued_keys.get(cell) == key:
                del self._queued_keys[cell]
                return key, cell
        raise IndexError("D* Lite 优先队列为空")

    def _successors(self, cell: Cell) -> tuple[Cell, ...]:
        assert self._context is not None
        if not self._context.traversable[cell]:
            return ()
        return tuple(iter_free_neighbors(self._context.traversable, cell))

    def _edge_cost(self, first: Cell, second: Cell) -> float:
        assert self._context is not None
        step = math.dist(first, second) * self._context.resolution_m
        clearance = float(self._context.clearance_m[second])
        target = self._context.inflation_radius_m + self.clearance_cost_margin_m
        crowding = 0.0
        if self.clearance_cost_margin_m > 0 and clearance < target:
            crowding = (target - clearance) / self.clearance_cost_margin_m
        return step * (1.0 + 9.0 * max(0.0, min(1.0, crowding)))

    def _update_vertex(self, cell: Cell) -> None:
        assert self._g is not None and self._rhs is not None and self._goal is not None
        if cell != self._goal:
            successors = self._successors(cell)
            self._rhs[cell] = min(
                (self._edge_cost(cell, item) + float(self._g[item]) for item in successors),
                default=math.inf,
            )
        self._remove(cell)
        if _different(float(self._g[cell]), float(self._rhs[cell])):
            self._push(cell)

    def _reset(self, context: MapContext, start: Cell, goal: Cell) -> None:
        self._context = context
        self._start = start
        self._last_start = start
        self._goal = goal
        self._km = 0.0
        self._g = np.full(context.shape, math.inf, dtype=np.float64)
        self._rhs = np.full(context.shape, math.inf, dtype=np.float64)
        self._rhs[goal] = 0.0
        self._queue = []
        self._queued_keys = {}
        self._counter = itertools.count()
        self._traversable_snapshot = context.traversable.copy()
        self._clearance_snapshot = context.clearance_m.copy()
        self._push(goal)

    def _can_reuse(self, context: MapContext, goal: Cell) -> bool:
        previous = self._context
        return bool(
            previous is not None
            and self._goal == goal
            and previous.shape == context.shape
            and previous.origin_xy_m == context.origin_xy_m
            and previous.resolution_m == context.resolution_m
            and previous.inflation_radius_m == context.inflation_radius_m
            and previous.robot_profile_version == context.robot_profile_version
        )

    def _apply_map_changes(self, context: MapContext) -> None:
        assert self._traversable_snapshot is not None
        assert self._clearance_snapshot is not None
        traversability_changed = self._traversable_snapshot != context.traversable
        clearance_changed = ~np.isclose(
            self._clearance_snapshot, context.clearance_m, rtol=0.0, atol=1e-12
        )
        changed = traversability_changed | clearance_changed
        self._context = context
        self._traversable_snapshot = context.traversable.copy()
        self._clearance_snapshot = context.clearance_m.copy()
        if not changed.any():
            return

        # 一个单元变化会影响以它为终点的边、它自身的出边，以及邻近对角边的
        # 穿角判定，因此更新其 8 邻域内所有顶点。
        affected: set[Cell] = set()
        rows, cols = context.shape
        for raw_row, raw_col in np.argwhere(changed):
            row, col = int(raw_row), int(raw_col)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    candidate = row + dy, col + dx
                    if 0 <= candidate[0] < rows and 0 <= candidate[1] < cols:
                        affected.add(candidate)
        for cell in affected:
            self._update_vertex(cell)

    def _compute_shortest_path(self) -> None:
        assert self._g is not None and self._rhs is not None and self._start is not None
        self.last_expanded_nodes = 0
        maximum_expansions = self._g.size * 50
        while (
            _key_less(self._top_key(), self._calculate_key(self._start))
            or _different(float(self._rhs[self._start]), float(self._g[self._start]))
        ):
            if self._top_key() == (math.inf, math.inf):
                break
            old_key, cell = self._pop()
            new_key = self._calculate_key(cell)
            if _key_less(old_key, new_key):
                self._push(cell)
            elif float(self._g[cell]) > float(self._rhs[cell]):
                self._g[cell] = self._rhs[cell]
                for predecessor in self._successors(cell):
                    self._update_vertex(predecessor)
            else:
                self._g[cell] = math.inf
                self._update_vertex(cell)
                for predecessor in self._successors(cell):
                    self._update_vertex(predecessor)
            self.last_expanded_nodes += 1
            if self.last_expanded_nodes > maximum_expansions:
                raise RuntimeError("D* Lite 未在有限次扩展内收敛")

    def _extract_cells(self) -> list[Cell] | None:
        assert self._g is not None and self._start is not None and self._goal is not None
        if not math.isfinite(float(self._g[self._start])):
            return None
        cells = [self._start]
        visited = {self._start}
        while cells[-1] != self._goal:
            current = cells[-1]
            candidates = []
            for successor in self._successors(current):
                value = self._edge_cost(current, successor) + float(self._g[successor])
                candidates.append(
                    (value, self._heuristic(successor, self._goal), successor)
                )
            if not candidates:
                return None
            _, _, selected = min(candidates)
            if selected in visited:
                return None
            cells.append(selected)
            visited.add(selected)
        return cells

    def plan(
        self,
        context: MapContext,
        start: Pose2D,
        goal: NavigationGoal,
    ) -> NavigationPlan | None:
        origin = world_to_cell(context, start.xy)
        destination = world_to_cell(context, goal.xy)
        if origin is None or destination is None:
            return None

        self.last_reused_state = self._can_reuse(context, destination)
        if not self.last_reused_state:
            self._reset(context, origin, destination)
        else:
            assert self._last_start is not None
            self._start = origin
            self._km += self._heuristic(self._last_start, origin)
            self._last_start = origin
            self._apply_map_changes(context)

        self._compute_shortest_path()
        cells = self._extract_cells()
        if cells is None:
            return None

        target_clearance = context.inflation_radius_m + self.clearance_cost_margin_m
        simplified = _simplify(context, cells, target_clearance)
        waypoints = tuple(cell_to_world(context, item) for item in simplified)
        if waypoints:
            waypoints = (start.xy, *waypoints[1:-1], goal.xy)
        length = sum(
            math.dist(first, second)
            for first, second in zip(waypoints, waypoints[1:])
        )
        return NavigationPlan(
            context.map_version,
            context.robot_profile_version,
            start,
            goal,
            tuple(cells),
            waypoints,
            length,
        )


__all__ = ["DStarLitePlanner"]
