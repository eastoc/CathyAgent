"""Best-effort CAD bounding-box helpers.

The production path prefers CadQuery/OCP BoundingBox APIs. Tests use a small
fake Workplane/Assembly, so this module also understands the fake object's
recorded operations well enough to validate non-empty package geometry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CadBoundingBox:
    """Axis-aligned bounding box in assembly coordinates."""

    xmin: float
    ymin: float
    zmin: float
    xmax: float
    ymax: float
    zmax: float

    @property
    def xlen(self) -> float:
        return self.xmax - self.xmin

    @property
    def ylen(self) -> float:
        return self.ymax - self.ymin

    @property
    def zlen(self) -> float:
        return self.zmax - self.zmin

    @property
    def valid(self) -> bool:
        return self.xlen > 0 and self.ylen > 0 and self.zlen > 0

    def translated(self, vector: tuple[float, float, float]) -> "CadBoundingBox":
        dx, dy, dz = vector
        return CadBoundingBox(
            self.xmin + dx,
            self.ymin + dy,
            self.zmin + dz,
            self.xmax + dx,
            self.ymax + dy,
            self.zmax + dz,
        )

    def union(self, other: "CadBoundingBox") -> "CadBoundingBox":
        return CadBoundingBox(
            min(self.xmin, other.xmin),
            min(self.ymin, other.ymin),
            min(self.zmin, other.zmin),
            max(self.xmax, other.xmax),
            max(self.ymax, other.ymax),
            max(self.zmax, other.zmax),
        )

    def to_dict(self) -> dict[str, float | bool]:
        return {
            "xmin": self.xmin,
            "ymin": self.ymin,
            "zmin": self.zmin,
            "xmax": self.xmax,
            "ymax": self.ymax,
            "zmax": self.zmax,
            "xlen": self.xlen,
            "ylen": self.ylen,
            "zlen": self.zlen,
            "valid": self.valid,
        }


def bounding_box_from_cad_object(cad_object: Any) -> CadBoundingBox | None:
    """Return a best-effort bounding box for a CadQuery-like object."""

    return (
        _bbox_from_cadquery_api(cad_object)
        or _bbox_from_build123d_api(cad_object)
        or _bbox_from_fake_assembly(cad_object)
        or _bbox_from_fake_workplane(cad_object)
    )


def _bbox_from_build123d_api(cad_object: Any) -> CadBoundingBox | None:
    method = getattr(cad_object, "bounding_box", None)
    if not callable(method):
        return None
    try:
        return _bbox_from_raw_boundbox(method())
    except Exception:
        return None


def _bbox_from_cadquery_api(cad_object: Any) -> CadBoundingBox | None:
    for value in _cadquery_bbox_candidates(cad_object):
        bbox = _bbox_from_raw_boundbox(value)
        if bbox is not None:
            return bbox
    return None


def _cadquery_bbox_candidates(cad_object: Any) -> list[Any]:
    candidates: list[Any] = []
    for attr in ("BoundingBox", "boundingBox"):
        method = getattr(cad_object, attr, None)
        if callable(method):
            try:
                candidates.append(method())
            except Exception:
                pass
    for attr in ("val", "toCompound"):
        method = getattr(cad_object, attr, None)
        if not callable(method):
            continue
        try:
            value = method()
        except Exception:
            continue
        for bbox_attr in ("BoundingBox", "boundingBox"):
            bbox_method = getattr(value, bbox_attr, None)
            if callable(bbox_method):
                try:
                    candidates.append(bbox_method())
                except Exception:
                    pass
    return candidates


def _bbox_from_raw_boundbox(raw: Any) -> CadBoundingBox | None:
    keys = {
        "xmin": ("xmin", "xMin"),
        "ymin": ("ymin", "yMin"),
        "zmin": ("zmin", "zMin"),
        "xmax": ("xmax", "xMax"),
        "ymax": ("ymax", "yMax"),
        "zmax": ("zmax", "zMax"),
    }
    values: dict[str, float] = {}
    for output_key, names in keys.items():
        for name in names:
            if hasattr(raw, name):
                values[output_key] = float(getattr(raw, name))
                break
    if len(values) == 6:
        return CadBoundingBox(**values)
    min_value = getattr(raw, "min", None)
    max_value = getattr(raw, "max", None)
    if min_value is not None and max_value is not None:
        min_tuple = _tuple3_from_vector(min_value)
        max_tuple = _tuple3_from_vector(max_value)
        if min_tuple is not None and max_tuple is not None:
            return CadBoundingBox(
                min_tuple[0],
                min_tuple[1],
                min_tuple[2],
                max_tuple[0],
                max_tuple[1],
                max_tuple[2],
            )
    add_method = getattr(raw, "Get", None)
    if callable(add_method):
        try:
            xmin, ymin, zmin, xmax, ymax, zmax = add_method()
            return CadBoundingBox(
                float(xmin),
                float(ymin),
                float(zmin),
                float(xmax),
                float(ymax),
                float(zmax),
            )
        except Exception:
            return None
    return None


def _bbox_from_fake_assembly(cad_object: Any) -> CadBoundingBox | None:
    parts = getattr(cad_object, "parts", None)
    if not isinstance(parts, list):
        return None
    bbox: CadBoundingBox | None = None
    for item in parts:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        part_bbox = bounding_box_from_cad_object(item[1])
        if part_bbox is None:
            continue
        loc = item[2] if len(item) > 2 else None
        translated = part_bbox.translated(_location_vector(loc))
        bbox = translated if bbox is None else bbox.union(translated)
    return bbox


def _bbox_from_fake_workplane(cad_object: Any) -> CadBoundingBox | None:
    root = getattr(cad_object, "root", cad_object)
    ops = getattr(root, "ops", None)
    if not isinstance(ops, list):
        return None
    boxes: list[CadBoundingBox] = []
    pending_circle_radius: float | None = None
    for op in ops:
        if not isinstance(op, tuple) or len(op) != 2:
            continue
        name, args = op
        if name == "box" and len(args) >= 3:
            boxes.append(_centered_box(float(args[0]), float(args[1]), float(args[2])))
        elif name == "circle" and args:
            pending_circle_radius = float(args[0])
        elif name == "extrude" and args and pending_circle_radius is not None:
            depth = float(args[0])
            radius = pending_circle_radius
            boxes.append(
                CadBoundingBox(
                    0.0,
                    -radius,
                    -radius,
                    depth,
                    radius,
                    radius,
                )
            )
            pending_circle_radius = None
        elif name == "translate" and args and boxes:
            vector = _tuple3(args[0])
            if vector is not None:
                boxes[-1] = boxes[-1].translated(vector)
    bbox: CadBoundingBox | None = None
    for item in boxes:
        bbox = item if bbox is None else bbox.union(item)
    return bbox


def _centered_box(length: float, width: float, height: float) -> CadBoundingBox:
    return CadBoundingBox(
        -length / 2.0,
        -width / 2.0,
        -height / 2.0,
        length / 2.0,
        width / 2.0,
        height / 2.0,
    )


def _location_vector(location: Any) -> tuple[float, float, float]:
    if isinstance(location, dict):
        return _tuple3(location.get("loc")) or (0.0, 0.0, 0.0)
    return _tuple3(location) or (0.0, 0.0, 0.0)


def _tuple3(value: Any) -> tuple[float, float, float] | None:
    if isinstance(value, (tuple, list)) and len(value) == 3:
        return (float(value[0]), float(value[1]), float(value[2]))
    return None


def _tuple3_from_vector(value: Any) -> tuple[float, float, float] | None:
    components = []
    for attr in ("X", "Y", "Z"):
        if hasattr(value, attr):
            components.append(getattr(value, attr))
    if len(components) != 3:
        for attr in ("x", "y", "z"):
            if hasattr(value, attr):
                components.append(getattr(value, attr))
        if len(components) > 3:
            components = components[-3:]
    if len(components) != 3:
        try:
            components = list(value)
        except TypeError:
            return None
    if len(components) != 3:
        return None
    return (float(components[0]), float(components[1]), float(components[2]))


__all__ = ["CadBoundingBox", "bounding_box_from_cad_object"]
