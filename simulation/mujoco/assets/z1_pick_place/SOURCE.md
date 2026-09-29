# Z1 tabletop scene provenance

`robot.xml` derives from `../unitree_z1/z1_gripper.xml` in Google DeepMind
MuJoCo Menagerie, commit `c96a32d28fb5da84da38c1da4d749e7a13212855`.
It reuses the vendored mesh assets via relative paths. See
[upstream provenance](../unitree_z1/SOURCE.md) and
[BSD-3-Clause license](../unitree_z1/LICENSE).

Local changes: base pose, TCP site, wrist camera with visual housing/bracket, named contact pads
with sliding friction 3, gripper actuator force range ±2, removal of the
upstream home keyframe because this scene adds free bodies. Geometry, body
inertias and six arm actuators retain upstream values.

`scene.xml` is the local table, three free cubes, open container, lighting,
fixed cameras and contact settings. Friction and actuator limits are tuned
for this deterministic simulation fixture; they are not measurements or
validated identification of real Unitree hardware. The wrist camera housing
and bracket are visual only: they add no mass or collision geometry.

The original `../unitree_z1` model files remain unchanged.
