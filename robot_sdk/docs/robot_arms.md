# Robot Arm Modeling

This guide covers common robot arm patterns for `robot_sdk`.

## Basic Chain

A serial robot arm is usually:

```text
base -> shoulder joint -> link1 -> elbow joint -> link2 -> wrist joint -> end link
```

In code:

```python
base = robot.link("base", inertial=...)
link1 = robot.link("link1", inertial=...)
shoulder = robot.joint("shoulder", "revolute", parent=base, child=link1, ...)
```

The link tree must have exactly one root in strict mode. The root is the link
that is not a child of any joint, usually `base`.

## Kinematics-First Pipeline

For new serial manipulators, prefer defining the mathematical model first and
compiling it into `RobotModel`. This keeps the kinematic intent separate from
the visual modeling pass:

```python
from robot_sdk import compile_serial_manipulator, demo_three_dof_spec


def build_robot_model():
    spec = demo_three_dof_spec()
    return compile_serial_manipulator(spec, name="three_dof_arm")
```

The compiler creates a connected link/joint tree, joint limits, actuators,
joint position sensors, simple placeholder visual/collision geometry, and
metadata that records the source DH row for each generated joint. Detailed
robot appearance and OBJ output should be added later by geometry helpers or
mesh-backed visual assets.

## Frame Convention

This section explains the frame convention behind the compiler and historical
primitive scaffolds. For new robot arms, use the kinematics-first pipeline
above instead of writing this code by hand unless you are doing a focused
geometry/assets pass.

For simple arms, use this convention:

- Put each link frame at the proximal joint.
- Make each link extend along positive x.
- Place box visual/collision geometry at half length.
- Put the next joint at full link length along x.

Example:

```python
length = 0.32
link_geom = Box((length, 0.05, 0.05))
link = robot.link("link1", inertial=inertial_from_box(link_geom, mass=1.0))
link.visual(link_geom, origin=Origin(xyz=(length / 2, 0, 0)))
link.collision(link_geom, origin=Origin(xyz=(length / 2, 0, 0)))

elbow = robot.joint(
    "elbow",
    "revolute",
    parent=link,
    child=next_link,
    origin=Origin(xyz=(length, 0, 0)),
    axis=(0, 0, 1),
    limit=JointLimit(lower=-2.5, upper=2.5, effort=30, velocity=3),
)
```

## 3DoF Pattern

A simple 3DoF arm can use:

- `shoulder`: yaw around z.
- `elbow`: yaw or pitch depending on the desired plane.
- `wrist_pitch`: pitch around y.

Use one actuator per driven joint and at least one joint position sensor for
each joint.

When the user asks for a new 3DoF arm, start from a `SerialManipulatorSpec`
and `compile_serial_manipulator(...)`. Add mesh-backed visuals in a separate
geometry/assets pass when OBJ output is required; collision and inertial data
can still use simplified primitive estimates.

## Motor and Gearbox Visuals

For robot arms, every driven `revolute` or `prismatic` joint should usually
have visible motor or gearbox geometry near the joint origin. The SDK
`Actuator` describes the control channel only; it does not automatically create
visible motor housings in URDF, MJCF, or viewers.

Recommended visual convention:

- Add a short `Cylinder` at revolute joints to represent a motor, gearbox, or
  bearing cap.
- Add a `Box` or `Cylinder` rail/carriage visual around prismatic joints.
- Attach the motor visual to the parent or child link near the joint origin.
- Keep motor collision simple, or omit separate motor collision if the main
  link collision already covers it.

Example:

```python
motor_geom = Cylinder(radius=0.055, length=0.04)
link1.visual(
    motor_geom,
    origin=Origin(xyz=(0.0, 0.0, 0.0), rpy=(0.0, 1.5708, 0.0)),
    material=aluminum,
    name="shoulder_motor_housing",
)
```

For a UR-style arm, do not leave the joints as bare intersecting boxes. Add
round shoulder, elbow, and wrist housings so the model reads as a real robot
mechanism rather than only a kinematic chain.

## 6DoF Pattern

For a 6DoF industrial-style arm, use a chain like:

```text
base
  shoulder_yaw
  shoulder_pitch
  elbow_pitch
  wrist_roll
  wrist_pitch
  wrist_yaw
  tool
```

Model the wrist as short links with clear joint origins. Do not collapse all
wrist joints into one link if the user asked for articulation.

Use role names that encode both the wrist order and intended axis, such as
`wrist_1_pitch`, `wrist_2_yaw`, and `wrist_3_roll`. The semantic compiler
derives link names from joint names, so a joint named `wrist_1` becomes
`wrist_1_link` in the compiled `RobotModel`.

For a UR3e-like scaffold, use the kinematics-first template:

```python
from robot_sdk import compile_serial_manipulator, ur3e_like_spec


def build_robot_model():
    spec = ur3e_like_spec()
    return compile_serial_manipulator(spec, name="ur3e_like")
```

This produces six revolute joints and these semantic links:

```text
base_link -> shoulder_link -> upper_arm_link -> forearm_link
  -> wrist_1_link -> wrist_2_link -> wrist_3_link -> tool0
```

## SCARA Pattern

A SCARA-like arm commonly uses:

- base revolute joint around z.
- elbow revolute joint around z.
- vertical prismatic joint along z.
- optional wrist revolute joint around z.

`prismatic` joints require `JointLimit` with lower/upper in meters.

## Grippers

For a parallel gripper:

- Create a palm/base link.
- Create one link per finger.
- Use prismatic joints for sliding jaws or revolute joints for pivoting jaws.
- Give opposing fingers mirrored joint axes or mirrored origins.

Add actuators to both finger joints unless modeling coupled motion manually.

## Modeling Quality Rules

- Keep the kinematic tree connected.
- Avoid floating links: every link except root must be the child of exactly one joint.
- Give every driven joint a visible motor/gearbox/bearing housing unless the user explicitly asks for a bare kinematic diagram.
- Prefer kinematics-first specs for serial arms; add OBJ assets in the geometry/assets pass rather than mixing visual design into the DH spec.
- Do not use visual mesh as collision when a simple primitive will do.
- Give realistic joint limits rather than arbitrary huge ranges.
- Use `probe_robot_model` before editing a large existing model.
- Use `compile_robot_model` after every meaningful edit.
