"""URDF exporter for ``RobotModel``.

URDF is a robot description format widely used by ROS. Unlike MJCF, URDF keeps
links and joints as top-level robot children; each joint connects one parent
link to one child link.
"""

from __future__ import annotations

from pathlib import Path
from xml.dom import minidom
from xml.etree import ElementTree as ET

from .model import Box, Collision, Cylinder, Geometry, Joint, Link, Mesh, RobotModel, Sphere, Visual


def export_urdf(model: RobotModel, path: str | Path | None = None, *, pretty: bool = True) -> str:
    """Export a ``RobotModel`` to URDF XML.

    If ``path`` is provided, the XML is written to disk as well as returned.
    The exporter assumes ``check_robot_model`` has already validated the model.
    """

    root = ET.Element("robot", {"name": model.name})
    for material in model.materials:
        material_el = ET.SubElement(root, "material", {"name": material.name})
        if material.rgba is not None:
            ET.SubElement(material_el, "color", {"rgba": _numbers(material.rgba)})

    for link in model.links:
        _add_link(root, link)
    for joint in model.joints:
        _add_joint(root, joint)

    xml = ET.tostring(root, encoding="unicode")
    if pretty:
        xml = minidom.parseString(xml).toprettyxml(indent="  ")
    if path is not None:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(xml, encoding="utf-8")
    return xml


def _add_link(root: ET.Element, link: Link) -> None:
    link_el = ET.SubElement(root, "link", {"name": link.name})

    if link.inertial is not None:
        inertial_el = ET.SubElement(link_el, "inertial")
        _add_origin(inertial_el, link.inertial.origin)
        ET.SubElement(inertial_el, "mass", {"value": _num(link.inertial.mass)})
        if link.inertial.inertia is not None:
            inertia = link.inertial.inertia
            ET.SubElement(
                inertial_el,
                "inertia",
                {
                    "ixx": _num(inertia.ixx),
                    "ixy": _num(inertia.ixy),
                    "ixz": _num(inertia.ixz),
                    "iyy": _num(inertia.iyy),
                    "iyz": _num(inertia.iyz),
                    "izz": _num(inertia.izz),
                },
            )

    for visual in link.visuals:
        visual_el = ET.SubElement(link_el, "visual", _name_attrs(visual.name))
        _add_origin(visual_el, visual.origin)
        _add_geometry(visual_el, visual.geometry)
        _add_visual_material(visual_el, visual)

    for collision in link.collisions:
        collision_el = ET.SubElement(link_el, "collision", _name_attrs(collision.name))
        _add_origin(collision_el, collision.origin)
        _add_geometry(collision_el, collision.geometry)


def _add_joint(root: ET.Element, joint: Joint) -> None:
    joint_type = _urdf_joint_type(joint)
    joint_el = ET.SubElement(root, "joint", {"name": joint.name, "type": joint_type})
    _add_origin(joint_el, joint.origin)
    ET.SubElement(joint_el, "parent", {"link": joint.parent})
    ET.SubElement(joint_el, "child", {"link": joint.child})

    if joint_type in {"revolute", "continuous", "prismatic"}:
        ET.SubElement(joint_el, "axis", {"xyz": _numbers(joint.axis)})
    if joint.limit is not None and joint_type in {"revolute", "prismatic"}:
        attrs = {"effort": _num(joint.limit.effort), "velocity": _num(joint.limit.velocity)}
        if joint.limit.lower is not None:
            attrs["lower"] = _num(joint.limit.lower)
        if joint.limit.upper is not None:
            attrs["upper"] = _num(joint.limit.upper)
        ET.SubElement(joint_el, "limit", attrs)
    if joint.dynamics is not None and joint_type in {"revolute", "continuous", "prismatic"}:
        ET.SubElement(
            joint_el,
            "dynamics",
            {"damping": _num(joint.dynamics.damping), "friction": _num(joint.dynamics.friction)},
        )


def _add_geometry(parent_el: ET.Element, geometry: Geometry) -> None:
    geometry_el = ET.SubElement(parent_el, "geometry")
    if isinstance(geometry, Box):
        ET.SubElement(geometry_el, "box", {"size": _numbers(geometry.size)})
    elif isinstance(geometry, Cylinder):
        ET.SubElement(geometry_el, "cylinder", {"radius": _num(geometry.radius), "length": _num(geometry.length)})
    elif isinstance(geometry, Sphere):
        ET.SubElement(geometry_el, "sphere", {"radius": _num(geometry.radius)})
    elif isinstance(geometry, Mesh):
        attrs = {"filename": geometry.filename}
        if geometry.scale:
            attrs["scale"] = _numbers(geometry.scale)
        ET.SubElement(geometry_el, "mesh", attrs)
    else:
        raise TypeError(f"unsupported geometry: {type(geometry).__name__}")


def _add_visual_material(visual_el: ET.Element, visual: Visual) -> None:
    material = visual.material
    if material is None:
        return
    if isinstance(material, str):
        ET.SubElement(visual_el, "material", {"name": material})
        return
    material_el = ET.SubElement(visual_el, "material", {"name": material.name})
    if material.rgba is not None:
        ET.SubElement(material_el, "color", {"rgba": _numbers(material.rgba)})


def _add_origin(parent_el: ET.Element, origin) -> None:
    if any(abs(v) > 0.0 for v in origin.xyz) or any(abs(v) > 0.0 for v in origin.rpy):
        ET.SubElement(parent_el, "origin", {"xyz": _numbers(origin.xyz), "rpy": _numbers(origin.rpy)})


def _urdf_joint_type(joint: Joint) -> str:
    if joint.joint_type == "free":
        return "floating"
    if joint.joint_type == "ball":
        raise ValueError("URDF does not support ball joints")
    return joint.joint_type


def _name_attrs(name: str | None) -> dict[str, str]:
    return {"name": name} if name else {}


def _num(value: float) -> str:
    return f"{float(value):.9g}"


def _numbers(values) -> str:
    return " ".join(_num(v) for v in values)
