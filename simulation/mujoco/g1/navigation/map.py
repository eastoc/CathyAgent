"""从 MuJoCo 场景先验生成 G1 二维静态代价地图。"""

from __future__ import annotations

import hashlib
import heapq
import math
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass
from typing import Any, Iterator

import numpy as np

from ..config import G1NavigationConfig, load_navigation_metadata
from ..contracts import Landmark, MapContext


NEIGHBORS = (
    (-1, 0),
    (1, 0),
    (0, -1),
    (0, 1),
    (-1, -1),
    (-1, 1),
    (1, -1),
    (1, 1),
)


def _numbers(value: str | None, length: int, default: tuple[float, ...]) -> tuple[float, ...]:
    if value is None:
        return default
    result = tuple(float(item) for item in value.split())
    if len(result) != length:
        raise ValueError(f"期望 {length} 个数值，实际为 {value!r}")
    return result


def _yaw_from_quat(value: str | None) -> float:
    w, x, y, z = _numbers(value, 4, (1.0, 0.0, 0.0, 0.0))
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def _rotate(x: float, y: float, yaw: float) -> tuple[float, float]:
    c, s = math.cos(yaw), math.sin(yaw)
    return c * x - s * y, s * x + c * y


@dataclass(frozen=True)
class _Geom2D:
    name: str
    kind: str
    center: tuple[float, float]
    yaw: float
    size: tuple[float, ...]

    def contains(self, x: np.ndarray, y: np.ndarray, padding: float) -> np.ndarray:
        dx, dy = x - self.center[0], y - self.center[1]
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        local_x = c * dx + s * dy
        local_y = -s * dx + c * dy
        if self.kind == "box":
            return (np.abs(local_x) <= self.size[0] + padding) & (
                np.abs(local_y) <= self.size[1] + padding
            )
        if self.kind == "cylinder":
            return local_x * local_x + local_y * local_y <= (self.size[0] + padding) ** 2
        raise ValueError(f"静态地图暂不支持障碍 geom 类型: {self.kind} ({self.name})")

    def bounds(self) -> tuple[tuple[float, float], tuple[float, float]]:
        if self.kind == "cylinder":
            radius = self.size[0]
            return (
                (self.center[0] - radius, self.center[1] - radius),
                (self.center[0] + radius, self.center[1] + radius),
            )
        sx, sy = self.size[:2]
        ex = abs(math.cos(self.yaw)) * sx + abs(math.sin(self.yaw)) * sy
        ey = abs(math.sin(self.yaw)) * sx + abs(math.cos(self.yaw)) * sy
        return (
            (self.center[0] - ex, self.center[1] - ey),
            (self.center[0] + ex, self.center[1] + ey),
        )


def _scene_geometries(scene_path) -> dict[str, _Geom2D]:
    root = ET.parse(scene_path).getroot()
    world = root.find("worldbody")
    if world is None:
        raise ValueError("MuJoCo 场景缺少 worldbody")
    result: dict[str, _Geom2D] = {}

    def visit(element: ET.Element, parent_xy: tuple[float, float], parent_yaw: float) -> None:
        px, py, _ = _numbers(element.get("pos"), 3, (0.0, 0.0, 0.0))
        offset = _rotate(px, py, parent_yaw)
        xy = parent_xy[0] + offset[0], parent_xy[1] + offset[1]
        yaw = parent_yaw + _yaw_from_quat(element.get("quat"))
        if element.get("euler") is not None:
            yaw += _numbers(element.get("euler"), 3, (0.0, 0.0, 0.0))[2]
        for geom in element.findall("geom"):
            name = geom.get("name")
            if not name:
                continue
            gx, gy, _ = _numbers(geom.get("pos"), 3, (0.0, 0.0, 0.0))
            rotated = _rotate(gx, gy, yaw)
            center = xy[0] + rotated[0], xy[1] + rotated[1]
            geom_yaw = yaw + _yaw_from_quat(geom.get("quat"))
            if geom.get("euler") is not None:
                geom_yaw += _numbers(geom.get("euler"), 3, (0.0, 0.0, 0.0))[2]
            kind = geom.get("type", "sphere")
            raw_size = tuple(float(item) for item in (geom.get("size") or "").split())
            result[name] = _Geom2D(name, kind, center, geom_yaw, raw_size)
        for body in element.findall("body"):
            visit(body, xy, yaw)

    visit(world, (0.0, 0.0), 0.0)
    return result


def _inflate(mask: np.ndarray, radius_cells: int) -> np.ndarray:
    if radius_cells <= 0:
        return mask.copy()
    result = mask.copy()
    rows, cols = mask.shape
    for dy in range(-radius_cells, radius_cells + 1):
        width = int(math.floor(math.sqrt(radius_cells * radius_cells - dy * dy)))
        src_r0, src_r1 = max(0, -dy), min(rows, rows - dy)
        for dx in range(-width, width + 1):
            src_c0, src_c1 = max(0, -dx), min(cols, cols - dx)
            result[src_r0 + dy : src_r1 + dy, src_c0 + dx : src_c1 + dx] |= mask[
                src_r0:src_r1, src_c0:src_c1
            ]
    return result


def _clearance(occupied: np.ndarray, resolution: float) -> np.ndarray:
    rows, cols = occupied.shape
    # 队列代价使用 Python float；这里必须保持 float64。float32 舍入后再用
    # 完全相等判断会把有效节点误判为旧条目，导致距离只传播一格便停止。
    distance = np.full((rows, cols), np.inf, dtype=np.float64)
    queue: list[tuple[float, int, int]] = []
    for row, col in np.argwhere(occupied):
        distance[row, col] = 0.0
        heapq.heappush(queue, (0.0, int(row), int(col)))
    while queue:
        value, row, col = heapq.heappop(queue)
        if value > float(distance[row, col]) + 1e-12:
            continue
        for dy, dx in NEIGHBORS:
            nr, nc = row + dy, col + dx
            if not (0 <= nr < rows and 0 <= nc < cols):
                continue
            candidate = value + math.hypot(dx, dy) * resolution
            if candidate < float(distance[nr, nc]):
                distance[nr, nc] = candidate
                heapq.heappush(queue, (candidate, nr, nc))
    return distance


def iter_free_neighbors(mask: np.ndarray, cell: tuple[int, int]) -> Iterator[tuple[int, int]]:
    row, col = cell
    rows, cols = mask.shape
    for dy, dx in NEIGHBORS:
        nr, nc = row + dy, col + dx
        if not (0 <= nr < rows and 0 <= nc < cols and mask[nr, nc]):
            continue
        if dy and dx and not (mask[row + dy, col] and mask[row, col + dx]):
            continue
        yield nr, nc


def _components(traversable: np.ndarray) -> np.ndarray:
    labels = np.full(traversable.shape, -1, dtype=np.int32)
    component = 0
    for seed_value in np.argwhere(traversable):
        seed = int(seed_value[0]), int(seed_value[1])
        if labels[seed] >= 0:
            continue
        labels[seed] = component
        queue = deque([seed])
        while queue:
            cell = queue.popleft()
            for neighbor in iter_free_neighbors(traversable, cell):
                if labels[neighbor] >= 0:
                    continue
                labels[neighbor] = component
                queue.append(neighbor)
        component += 1
    return labels


class StaticMapBuilder:
    def __init__(self, config: G1NavigationConfig) -> None:
        self.config = config

    def build(self) -> MapContext:
        metadata = load_navigation_metadata(self.config)
        map_raw = dict(metadata.get("map") or {})
        minimum = tuple(float(item) for item in map_raw["bounds_min_xy_m"])
        maximum = tuple(float(item) for item in map_raw["bounds_max_xy_m"])
        resolution = float(map_raw["resolution_m"])
        cols = int(math.ceil((maximum[0] - minimum[0]) / resolution))
        rows = int(math.ceil((maximum[1] - minimum[1]) / resolution))
        if rows <= 0 or cols <= 0:
            raise ValueError("地图范围无效")
        geometries = _scene_geometries(self.config.scene_path)
        obstacle_names = tuple(str(item) for item in metadata.get("static_obstacles") or ())
        missing = [name for name in obstacle_names if name not in geometries]
        if missing:
            raise ValueError(f"navigation.yaml 引用了不存在的障碍: {missing}")

        x = minimum[0] + (np.arange(cols) + 0.5) * resolution
        y = minimum[1] + (np.arange(rows) + 0.5) * resolution
        grid_x, grid_y = np.meshgrid(x, y)
        ground = np.ones((rows, cols), dtype=bool)
        occupied = np.zeros((rows, cols), dtype=bool)
        names = np.full((rows, cols), "", dtype="<U64")
        cell_padding = resolution * math.sqrt(2.0) / 2.0
        for name in obstacle_names:
            selected = geometries[name].contains(grid_x, grid_y, cell_padding)
            occupied |= selected
            names[selected & (names == "")] = name
        # 地图边界外不可通行；边缘单元按实体边界处理，防止圆形包络越界。
        occupied[[0, -1], :] = True
        occupied[:, [0, -1]] = True
        names[[0, -1], :] = "map_boundary"
        names[:, [0, -1]] = "map_boundary"

        inflation_cells = int(math.ceil(self.config.inflation_radius_m / resolution))
        inflated = _inflate(occupied, inflation_cells)
        traversable = ground & ~inflated
        clearance = _clearance(occupied, resolution)
        components = _components(traversable)

        landmarks: list[Landmark] = []
        for raw in metadata.get("landmarks") or ():
            value = dict(raw)
            geom_name = str(value["geom"])
            if geom_name not in geometries:
                raise ValueError(f"landmark {value.get('name')} 引用了不存在的 geom: {geom_name}")
            geom = geometries[geom_name]
            landmarks.append(
                Landmark(
                    name=str(value["name"]),
                    label=str(value.get("label") or value["name"]),
                    geom=geom_name,
                    center_xy_m=geom.center,
                    bounds_xy_m=geom.bounds(),
                    navigable_surface=bool(value.get("navigable_surface", False)),
                )
            )

        digest = hashlib.sha256()
        digest.update(occupied.tobytes())
        digest.update(str(self.config.inflation_radius_m).encode("ascii"))
        version = f"{self.config.map_version}:{digest.hexdigest()[:12]}"
        for array in (ground, occupied, traversable, clearance, components, names):
            array.setflags(write=False)
        return MapContext(
            map_version=version,
            robot_profile_version=self.config.robot_profile_version,
            frame=str(metadata.get("frame") or "world"),
            resolution_m=resolution,
            origin_xy_m=minimum,  # type: ignore[arg-type]
            bounds_min_xy_m=minimum,  # type: ignore[arg-type]
            bounds_max_xy_m=maximum,  # type: ignore[arg-type]
            inflation_radius_m=self.config.inflation_radius_m,
            ground_mask=ground,
            occupied=occupied,
            traversable=traversable,
            clearance_m=clearance,
            component_ids=components,
            landmarks=tuple(landmarks),
            obstacle_name_by_cell=names,
        )


def world_to_cell(context: MapContext, xy: tuple[float, float]) -> tuple[int, int] | None:
    col = math.floor((xy[0] - context.origin_xy_m[0]) / context.resolution_m)
    row = math.floor((xy[1] - context.origin_xy_m[1]) / context.resolution_m)
    if 0 <= row < context.shape[0] and 0 <= col < context.shape[1]:
        return row, col
    return None


def cell_to_world(context: MapContext, cell: tuple[int, int]) -> tuple[float, float]:
    row, col = cell
    return (
        context.origin_xy_m[0] + (col + 0.5) * context.resolution_m,
        context.origin_xy_m[1] + (row + 0.5) * context.resolution_m,
    )


__all__ = [
    "NEIGHBORS",
    "StaticMapBuilder",
    "cell_to_world",
    "iter_free_neighbors",
    "world_to_cell",
]
