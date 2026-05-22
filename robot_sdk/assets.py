"""Managed mesh assets and optional CadQuery tessellation.
把 CAD/网格数据落成 OBJ 文件，并给出 Mesh 供 RobotModel 引用。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Tuple

from .model import Mesh

Vec3 = Tuple[float, float, float]
Face = Tuple[int, int, int]


@dataclass(frozen=True)
class MeshExport:
    """Result of materializing a procedural mesh into the asset workspace."""

    mesh: Mesh
    vertices: list[Vec3]
    faces: list[Face]
    local_aabb: tuple[Vec3, Vec3]


class AssetSession:
    """Owns generated mesh files for one robot build."""

    def __init__(self, root: str | os.PathLike[str], *, mesh_subdir: str = "assets/meshes") -> None:
        self.root = Path(root).expanduser().resolve()
        self.mesh_dir = self.root / mesh_subdir
        self.mesh_dir.mkdir(parents=True, exist_ok=True)

    def mesh_path(self, name: str, *, suffix: str = ".obj") -> Path:
        safe = _safe_stem(name)
        return self.mesh_dir / f"{safe}{suffix}"

    def mesh_ref(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()


def mesh_from_vertices(
    vertices: list[Vec3],
    faces: list[Face],
    name: str,
    *,
    assets: AssetSession | str | os.PathLike[str] | None = None,
) -> MeshExport:
    """Write vertices/faces as OBJ and return a ``Mesh`` reference."""

    session = _asset_session(assets)
    path = session.mesh_path(name, suffix=".obj")
    _write_obj(path, vertices=vertices, faces=faces)
    aabb = _local_aabb(vertices)
    mesh = Mesh(filename=session.mesh_ref(path), name=_safe_stem(name), materialized_path=path.as_posix())
    return MeshExport(mesh=mesh, vertices=vertices, faces=faces, local_aabb=aabb)


def mesh_from_cadquery(
    model: object,
    name: str,
    *,
    assets: AssetSession | str | os.PathLike[str] | None = None,
    tolerance: float = 0.001,
    angular_tolerance: float = 0.1,
    unit_scale: float = 1.0,
) -> MeshExport:
    """Tessellate a CadQuery Shape/Workplane/Assembly into a managed OBJ mesh."""
    cq = _require_cadquery()
    shape = _coerce_cadquery_shape(model, cq)
    vertices, faces = _tessellate_shape(
        shape,
        tolerance=tolerance,
        angular_tolerance=angular_tolerance,
        unit_scale=unit_scale,
    )
    digest = _mesh_digest(vertices, faces)
    return mesh_from_vertices(vertices, faces, f"{name}_{digest[:10]}", assets=assets)


def _asset_session(assets: AssetSession | str | os.PathLike[str] | None) -> AssetSession:
    if isinstance(assets, AssetSession):
        return assets
    return AssetSession(assets or Path.cwd())


def _require_cadquery() -> Any:
    try:
        import cadquery as cq  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("CadQuery support requires the cadquery package") from exc
    return cq


def _coerce_cadquery_shape(model: object, cq: Any) -> object:
    if isinstance(model, cq.Assembly):
        return model.toCompound()
    if isinstance(model, cq.Workplane):
        values = list(model.vals())
        if not values:
            raise TypeError("CadQuery Workplane produced no shapes")
        if len(values) == 1:
            return values[0]
        return cq.Compound.makeCompound(values)
    if isinstance(model, cq.Shape):
        return model
    raise TypeError("Expected cadquery.Shape, cadquery.Workplane, or cadquery.Assembly")


def _tessellate_shape(
    shape: object,
    *,
    tolerance: float,
    angular_tolerance: float,
    unit_scale: float,
) -> tuple[list[Vec3], list[Face]]:
    try:
        raw_vertices, raw_faces = shape.tessellate(float(tolerance), float(angular_tolerance))
    except TypeError:
        raw_vertices, raw_faces = shape.tessellate(float(tolerance))
    scale = float(unit_scale)
    vertices = [tuple(coord * scale for coord in _vector_xyz(vertex)) for vertex in raw_vertices]
    faces = [(int(face[0]), int(face[1]), int(face[2])) for face in raw_faces]
    if not vertices or not faces:
        raise ValueError("CadQuery tessellation produced an empty mesh")
    return vertices, faces


def _vector_xyz(value: Any) -> Vec3:
    if hasattr(value, "x") and hasattr(value, "y") and hasattr(value, "z"):
        return (float(value.x), float(value.y), float(value.z))
    if isinstance(value, (tuple, list)) and len(value) == 3:
        return (float(value[0]), float(value[1]), float(value[2]))
    raise TypeError(f"Unsupported vertex type: {type(value).__name__}")


def _write_obj(path: Path, *, vertices: list[Vec3], faces: list[Face]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for x, y, z in vertices:
        lines.append(f"v {x:.6f} {y:.6f} {z:.6f}")
    for a, b, c in faces:
        lines.append(f"f {a + 1} {b + 1} {c + 1}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _local_aabb(vertices: list[Vec3]) -> tuple[Vec3, Vec3]:
    if not vertices:
        raise ValueError("cannot compute aabb for empty vertices")
    mn = (
        min(v[0] for v in vertices),
        min(v[1] for v in vertices),
        min(v[2] for v in vertices),
    )
    mx = (
        max(v[0] for v in vertices),
        max(v[1] for v in vertices),
        max(v[2] for v in vertices),
    )
    return mn, mx


def _mesh_digest(vertices: list[Vec3], faces: list[Face]) -> str:
    digest = hashlib.sha256()
    for vertex in vertices:
        digest.update(f"v:{vertex[0]:.9f},{vertex[1]:.9f},{vertex[2]:.9f};".encode("utf-8"))
    for face in faces:
        digest.update(f"f:{face[0]},{face[1]},{face[2]};".encode("utf-8"))
    return digest.hexdigest()


def _safe_stem(name: str) -> str:
    stem = Path(str(name)).stem.strip() or "mesh"
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in stem)
