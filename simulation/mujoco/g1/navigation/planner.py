"""在膨胀栅格上的八邻域 A* 与路径简化。"""

from __future__ import annotations

import heapq
import math

from ..contracts import MapContext, NavigationGoal, NavigationPlan, Pose2D
from .map import cell_to_world, iter_free_neighbors, world_to_cell


def _octile(a: tuple[int, int], b: tuple[int, int]) -> float:
    dy, dx = abs(a[0] - b[0]), abs(a[1] - b[1])
    return min(dx, dy) * math.sqrt(2.0) + abs(dx - dy)


def _line_cells(start: tuple[int, int], end: tuple[int, int]) -> tuple[tuple[int, int], ...]:
    dy, dx = end[0] - start[0], end[1] - start[1]
    count = max(abs(dx), abs(dy)) * 2 + 1
    result: list[tuple[int, int]] = []
    for index in range(count + 1):
        alpha = index / count
        cell = round(start[0] + alpha * dy), round(start[1] + alpha * dx)
        if not result or cell != result[-1]:
            result.append(cell)
    return tuple(result)


def _line_free(
    context: MapContext,
    start: tuple[int, int],
    end: tuple[int, int],
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
    cells: list[tuple[int, int]],
    minimum_clearance_m: float,
) -> list[tuple[int, int]]:
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


class AStarPlanner:
    def __init__(self, clearance_cost_margin_m: float = 0.2) -> None:
        self.clearance_cost_margin_m = clearance_cost_margin_m

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
        queue: list[tuple[float, float, tuple[int, int]]] = [(0.0, 0.0, origin)]
        costs = {origin: 0.0}
        parents: dict[tuple[int, int], tuple[int, int]] = {}
        target_clearance = context.inflation_radius_m + self.clearance_cost_margin_m
        while queue:
            _, cost, cell = heapq.heappop(queue)
            if cost != costs.get(cell):
                continue
            if cell == destination:
                cells = [cell]
                while cells[-1] != origin:
                    cells.append(parents[cells[-1]])
                cells.reverse()
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
            for neighbor in iter_free_neighbors(context.traversable, cell):
                step = math.dist(cell, neighbor) * context.resolution_m
                clearance = float(context.clearance_m[neighbor])
                crowding = 0.0
                if self.clearance_cost_margin_m > 0 and clearance < target_clearance:
                    crowding = (target_clearance - clearance) / self.clearance_cost_margin_m
                candidate = cost + step * (1.0 + 9.0 * max(0.0, min(1.0, crowding)))
                if candidate >= costs.get(neighbor, math.inf):
                    continue
                costs[neighbor] = candidate
                parents[neighbor] = cell
                heuristic = _octile(neighbor, destination) * context.resolution_m
                heapq.heappush(queue, (candidate + heuristic, candidate, neighbor))
        return None


__all__ = ["AStarPlanner"]
