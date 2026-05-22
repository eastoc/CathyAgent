# Robot SDK Concepts

This document is the short conceptual reference for authoring `robot_model.py`.
Use the public APIs exported from `robot_sdk`; do not import private modules.

## RobotModel

`RobotModel` is the top-level robot description. A valid model is a set of
links connected by joints, optionally enriched with materials, actuators, and
sensors.

```python
from robot_sdk import RobotModel

robot = RobotModel("my_robot")
```

The expected entry point for agent-authored files is:

```python
def build_robot_model() -> RobotModel:
    robot = RobotModel("my_robot")
    return robot


robot_model = build_robot_model()
```

## Link

A `Link` is one rigid body in the kinematic tree. Every physical segment of a
robot arm should normally be one link.

Each link can have:

- `visuals`: geometry for rendering.
- `collisions`: geometry for contact/collision.
- `inertial`: mass and inertia used by simulators.

Prefer giving every link at least one visual, one collision, and one inertial.
Missing fields are warnings from `check_robot_model`, not hard errors.

## Geometry

Primitive geometries are:

- `Box(size=(x, y, z))`: full extents in meters.
- `Cylinder(radius=r, length=l)`: cylinder aligned to local z.
- `Sphere(radius=r)`.
- `Mesh(filename=..., name=..., scale=...)`: external or generated mesh.

Use primitives for simple structural links, mounts, bases, and approximate
collision. Use `Mesh` for complex visible geometry.

## Origin

`Origin` is the pose of an item relative to its parent frame.

- `xyz`: translation in meters.
- `rpy`: roll/pitch/yaw in radians.

Common usage:

- `Visual.origin`: geometry relative to the link frame.
- `Collision.origin`: collision geometry relative to the link frame.
- `Joint.origin`: child joint frame relative to the parent link frame.
- `Inertial.origin`: center of mass relative to the link frame.

For a box link of length `L` along x, place the visual/collision center at
`Origin(xyz=(L / 2, 0, 0))` when the link frame is at the proximal joint.

## Visual vs Collision vs Inertial

`Visual` is what the robot looks like. It may be detailed and mesh-backed.

`Collision` is what the simulator uses for contact. It should usually be simple
and stable, even when visual geometry is complex.

`Inertial` contains mass and inertia. Use helpers such as `inertial_from_box`
and `inertial_from_cylinder` for early design loops.

## Joint

`Joint` connects a parent link to a child link.

Common joint types:

- `revolute`: bounded rotation, requires `JointLimit`.
- `continuous`: unbounded rotation.
- `prismatic`: bounded translation, requires `JointLimit`.
- `fixed`: no motion.

For moving joints, set a non-zero `axis`. Use simple canonical axes unless the
mechanism requires otherwise: `(0, 0, 1)`, `(0, 1, 0)`, or `(1, 0, 0)`.

## Actuator and Sensor

`Actuator` describes how a joint is driven. A driven joint usually needs one
actuator:

```python
robot.actuator("shoulder_motor", shoulder, ctrl_range=(-1, 1), force_range=(-30, 30))
```

An `Actuator` is not a visible motor body. It does not create a motor housing,
gearbox, bearing, or cap in URDF/MJCF. If the robot should visibly contain a
motor, add ordinary `Visual` geometry near the joint origin, usually a
`Cylinder` or `Box` named like `shoulder_motor_housing`.

`Sensor` describes what state is observed. For robot arms, joint position
sensors are the most common:

```python
robot.sensor("shoulder_pos", "jointpos", "shoulder")
```
