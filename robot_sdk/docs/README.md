# Robot SDK Agent Guide

This directory is the canonical robot_sdk reference for CathyAgent workflows.

Read these files when building or revising `robot_model.py`:

- `concepts.md`: core API concepts and coordinate conventions.
- `robot_arms.md`: serial arms, SCARA arms, wrists, and grippers.
- `assets.md`: managed OBJ/Mesh workflows.
- `export.md`: MJCF/URDF behavior and differences.
- `troubleshooting.md`: validation errors, warnings, and repair strategy.

For serial arms, prefer the three-layer pipeline when possible:

```text
SerialManipulatorSpec
  -> compile_serial_manipulator
  -> RobotModel
  -> compile_robot_model
```

The intended workflow is:

```text
create_robot_template
  -> edit robot_model.py with robot_sdk APIs
  -> probe_robot_model when structure is unclear
  -> compile_robot_model after edits
  -> fix checks until ok=true
```
