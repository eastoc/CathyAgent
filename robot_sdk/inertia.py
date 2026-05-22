"""Simple inertial estimators for early robot design loops."""

from __future__ import annotations

from .model import Box, Cylinder, Inertia, Inertial, Sphere


def inertial_from_box(box: Box, *, mass: float) -> Inertial:
    x, y, z = box.size
    ixx = (1.0 / 12.0) * mass * (y * y + z * z)
    iyy = (1.0 / 12.0) * mass * (x * x + z * z)
    izz = (1.0 / 12.0) * mass * (x * x + y * y)
    return Inertial(mass=mass, inertia=Inertia(ixx=ixx, ixy=0.0, ixz=0.0, iyy=iyy, iyz=0.0, izz=izz))


def inertial_from_cylinder(cylinder: Cylinder, *, mass: float) -> Inertial:
    r = cylinder.radius
    length = cylinder.length
    ixx = (1.0 / 12.0) * mass * (3.0 * r * r + length * length)
    iyy = ixx
    izz = 0.5 * mass * r * r
    return Inertial(mass=mass, inertia=Inertia(ixx=ixx, ixy=0.0, ixz=0.0, iyy=iyy, iyz=0.0, izz=izz))


def inertial_from_sphere(sphere: Sphere, *, mass: float) -> Inertial:
    value = (2.0 / 5.0) * mass * sphere.radius * sphere.radius
    return Inertial(mass=mass, inertia=Inertia(ixx=value, ixy=0.0, ixz=0.0, iyy=value, iyz=0.0, izz=value))
