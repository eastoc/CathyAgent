# Troubleshooting

Use `compile_robot_model` after edits. Treat its `checks` and `summary` output
as the source of truth.

## Common Errors

### `no_links`

The model has no links. Create at least one root link:

```python
base = robot.link("base")
```

### `missing_joint_limit`

`revolute` and `prismatic` joints require `JointLimit`.

```python
limit=JointLimit(lower=-1.57, upper=1.57, effort=20.0, velocity=3.0)
```

### `continuous_joint_limits`

A `continuous` joint can have effort/velocity metadata, but it must not set
`lower` or `upper`.

### `unsupported_joint_limit`

`fixed`, `ball`, and `free` joints do not support limits in this SDK.

### `zero_joint_axis`

Moving joints need a non-zero axis:

```python
axis=(0.0, 0.0, 1.0)
```

### `missing_parent` / `missing_child`

A joint references a link that does not exist. Fix the link name or create the
missing link.

### `multiple_parent_joints`

A link is the child of more than one joint. In a tree robot model, every link
except the root has exactly one parent joint.

### `multiple_root_links`

More than one link is not connected as a child. Add joints to connect all links
under a single root.

### `unreachable_links`

Some links are not reachable from the root. Check joint parent/child names and
make sure all links belong to one connected tree.

### `actuator_missing_joint`

An actuator references a missing joint. Bind it to an existing `Joint` object or
joint name.

### `sensor_missing_joint` / `sensor_missing_link`

Joint sensors (`jointpos`, `jointvel`) must target joints. Body sensors (`imu`,
`force`, `touch`, `camera`) must target links.

## Common Warnings

### `link_without_visual`

The link has no visible geometry. Add `link.visual(...)`.

### `link_without_collision`

The link has no collision geometry. Add a simple `link.collision(...)`.

### `link_without_inertial`

The link has no mass. Use `inertial_from_box`, `inertial_from_cylinder`, or
`inertial_from_sphere`.

## Agent Repair Strategy

1. Run `probe_robot_model` when the structure is unclear.
2. Fix blocking `error` issues before tuning warnings.
3. Prefer structural fixes over suppressing checks.
4. Re-run `compile_robot_model` after each coherent edit.
5. Return artifact paths only after `ok=true`.
