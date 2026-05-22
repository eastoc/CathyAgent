# Mesh Assets

`robot_sdk.assets` turns CAD or raw mesh data into managed OBJ files and returns
`Mesh` objects that can be attached to links.

For agent-authored robot arm models, default to generating mesh-backed visual
assets for the main visible parts so the compile result includes OBJ files.
Keep collision and inertial data as simplified primitives unless the user asks
for detailed collision meshes.

## Default OBJ Workflow

If the user asks to create or revise a robot arm, produce OBJ visual assets by
default. Do not wait for a separate "export OBJ" request. If the current model
only uses primitives such as `Box`, `Cylinder`, or `Sphere`, convert the main
visible arm bodies and motor housings to mesh-backed visuals. Primitive geometry
is exported directly to MJCF/URDF and does not create OBJ files by itself.

Recommended agent workflow:

1. Keep `collision` and `inertial` as simplified primitives.
2. Create `assets = AssetSession("build/robot")` inside `build_robot_model()`.
3. Use `mesh_from_vertices(...)` or `mesh_from_cadquery(...)` to generate OBJ.
4. Attach `mesh_export.mesh` with `link.visual(...)`.
5. Run `compile_robot_model` and report `obj_paths`, MJCF, URDF, and report paths.

Good user-facing interpretation:

```text
Make link1 visual mesh-backed and generate OBJ; keep collision and inertial as Box.
```

If `compile_robot_model` returns an empty `obj_paths` list for a newly modeled
arm, revise `robot_model.py` to add mesh-backed visuals and compile again.

## When to Use Mesh

Use `Mesh` for:

- rounded housings, brackets, covers, grippers, or tool heads;
- CAD-derived parts;
- visual geometry with holes, fillets, shells, or non-box silhouettes.

Keep collision simple:

- visual: detailed mesh;
- collision: simplified `Box`, `Cylinder`, or `Sphere`;
- inertial: simplified primitive estimate.

## AssetSession

`AssetSession` owns generated files for one robot build.

```python
from robot_sdk import AssetSession

assets = AssetSession("build/robot")
```

By default, mesh files are written under:

```text
build/robot/assets/meshes/
```

The returned `Mesh.filename` is relative to the asset root, for example:

```text
assets/meshes/link1_body.obj
```

## From Vertices

```python
from robot_sdk import AssetSession, mesh_from_vertices

assets = AssetSession("build/robot")
mesh_export = mesh_from_vertices(
    vertices=[(0, 0, 0), (0.1, 0, 0), (0, 0.1, 0)],
    faces=[(0, 1, 2)],
    name="triangle_panel",
    assets=assets,
)

link.visual(mesh_export.mesh)
```

`mesh_export` includes:

- `mesh`: the `Mesh` object for `link.visual(...)`.
- `vertices` and `faces`: materialized mesh data.
- `local_aabb`: local bounding box.

## From CadQuery

```python
import cadquery as cq
from robot_sdk import AssetSession, mesh_from_cadquery

assets = AssetSession("build/robot")
part = cq.Workplane("XY").box(0.3, 0.05, 0.05).edges("|Z").fillet(0.01)
mesh_export = mesh_from_cadquery(part, "link1_body", assets=assets)
link.visual(mesh_export.mesh)
```

CadQuery is optional. If it is not installed, `mesh_from_cadquery` raises a
runtime error.

## Export Behavior

MJCF exporter writes:

```xml
<asset>
  <mesh name="link1_body" file="assets/meshes/link1_body.obj"/>
</asset>
```

URDF exporter writes:

```xml
<mesh filename="assets/meshes/link1_body.obj"/>
```

Do not hand-edit generated OBJ, MJCF, or URDF files during agent workflows.
Edit `robot_model.py` and re-run `compile_robot_model`.
