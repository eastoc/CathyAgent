# Exporting MJCF and URDF

`compile_robot_model` is the preferred export path for agent workflows. It
loads `robot_model.py`, validates the `RobotModel`, then writes MJCF and URDF
when checks pass.

Do not ask the agent to hand-write MJCF or URDF.
OBJ files are produced before export when `robot_model.py` uses
`AssetSession` plus mesh-backed visuals.

## Direct API

For Python callers:

```python
from robot_sdk import check_robot_model, export_mjcf, export_urdf

robot = build_robot_model()
report = check_robot_model(robot)
report.assert_ok()
export_mjcf(robot, "build/robot/my_robot.xml")
export_urdf(robot, "build/robot/my_robot.urdf")
```

For CathyAgent tools, use:

```text
compile_robot_model(path="robot_model.py", output_dir="build/robot")
```

## What Gets Exported

Both MJCF and URDF export:

- links;
- visuals;
- collisions;
- inertial mass and inertia;
- joints;
- primitive geometry;
- mesh geometry.

MJCF additionally exports:

- actuators;
- supported sensors;
- MuJoCo joint dynamics attributes such as damping, frictionloss, and armature.

URDF exports:

- joint limits;
- joint dynamics damping/friction;
- materials and visual colors.

URDF does not represent the full MuJoCo actuator model. Keep actuator behavior
in MJCF when simulation control is required.

## Geometry Differences

`Box`:

- `robot_sdk`: full extents.
- MJCF: half extents in `size`.
- URDF: full extents in `box size`.

`Cylinder`:

- `robot_sdk`: radius and full length.
- MJCF: radius and half length.
- URDF: radius and full length.

`Origin`:

- MJCF: `pos` and `euler`.
- URDF: `<origin xyz="..." rpy="..."/>`.

## Joint Differences

`revolute`, `continuous`, `prismatic`, and `fixed` are supported by URDF.

`free` maps to URDF `floating`.

`ball` has no direct URDF equivalent in this exporter and raises an error.

## Compile Outputs

Successful compile returns paths like:

```json
{
  "ok": true,
  "mjcf_path": "build/robot/robot.xml",
  "urdf_path": "build/robot/robot.urdf",
  "obj_paths": ["build/robot/assets/meshes/link1_body.obj"],
  "report_path": "build/robot/robot.checks.json"
}
```

When validation fails, export paths are `null` and `summary` contains
actionable issues and suggestions.
