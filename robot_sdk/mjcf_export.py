"""MuJoCo MJCF exporter for RobotModel."""

from __future__ import annotations

from pathlib import Path
from xml.dom import minidom
from xml.etree import ElementTree as ET

from .model import Box, Collision, Cylinder, Geometry, Joint, Link, Mesh, RobotModel, Sphere, Visual


def export_mjcf(model: RobotModel, path: str | Path | None = None, *, pretty: bool = True) -> str:
    """Export a ``RobotModel`` to MuJoCo MJCF XML.

    If ``path`` is provided, the XML is written to disk as well as returned.
    The exporter assumes ``check_robot_model`` has already validated the model.
    """

    root = ET.Element("mujoco", {"model": model.name})
    ET.SubElement(root, "compiler", {"angle": "radian", "coordinate": "local"})
    ET.SubElement(root, "option", {"timestep": "0.002"})

    asset_el = ET.SubElement(root, "asset")
    _add_materials(asset_el, model)
    mesh_names = _add_mesh_assets(asset_el, model)

    worldbody = ET.SubElement(root, "worldbody")
    link_by_name = {link.name: link for link in model.links}
    children = _children_by_parent(model.joints)
    root_links = _root_links(model)
    for link in root_links:
        _emit_body(worldbody, link, children, link_by_name, mesh_names)

    _add_actuators(root, model)
    _add_sensors(root, model)

    xml = ET.tostring(root, encoding="unicode")
    if pretty:
        xml = minidom.parseString(xml).toprettyxml(indent="  ")
    if path is not None:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(xml, encoding="utf-8")
    return xml


def _add_materials(asset_el: ET.Element, model: RobotModel) -> None:
    for material in model.materials:
        attrs = {"name": material.name}
        if material.rgba is not None:
            attrs["rgba"] = _numbers(material.rgba)
        ET.SubElement(asset_el, "material", attrs)


def _add_mesh_assets(asset_el: ET.Element, model: RobotModel) -> dict[str, str]:
    mesh_names: dict[str, str] = {}
    for link in model.links:
        for geometry in _iter_geometries(link):
            if not isinstance(geometry, Mesh):
                continue
            mesh_name = geometry.name or Path(geometry.filename).stem
            if geometry.filename not in mesh_names:
                ET.SubElement(asset_el, "mesh", {"name": mesh_name, "file": geometry.filename})
                mesh_names[geometry.filename] = mesh_name
    return mesh_names


def _children_by_parent(joints: list[Joint]) -> dict[str, list[Joint]]:
    out: dict[str, list[Joint]] = {}
    for joint in joints:
        out.setdefault(joint.parent, []).append(joint)
    return out


def _root_links(model: RobotModel) -> list[Link]:
    child_names = {joint.child for joint in model.joints}
    roots = [link for link in model.links if link.name not in child_names]
    return roots or list(model.links[:1])


def _emit_body(
    parent_el: ET.Element,
    link: Link,
    children: dict[str, list[Joint]],
    link_by_name: dict[str, Link],
    mesh_names: dict[str, str],
    *,
    incoming_joint: Joint | None = None,
) -> None:
    """Emit one link body and recursively emit its child bodies."""

    attrs = {"name": link.name}
    if incoming_joint is not None:
        attrs.update(_origin_attrs(incoming_joint.origin))
    body_el = ET.SubElement(parent_el, "body", attrs)

    if link.inertial is not None:
        inertial_attrs = {"mass": _num(link.inertial.mass)}
        inertial_attrs.update(_origin_attrs(link.inertial.origin))
        if link.inertial.inertia is not None:
            inertial_attrs["diaginertia"] = _numbers(
                (
                    link.inertial.inertia.ixx,
                    link.inertial.inertia.iyy,
                    link.inertial.inertia.izz,
                )
            )
        ET.SubElement(body_el, "inertial", inertial_attrs)

    if incoming_joint is not None and incoming_joint.joint_type != "fixed":
        ET.SubElement(body_el, "joint", _joint_attrs(incoming_joint))

    for visual in link.visuals:
        ET.SubElement(body_el, "geom", _geom_attrs(visual, mesh_names, group="1", contype="0", conaffinity="0"))
    for collision in link.collisions:
        ET.SubElement(body_el, "geom", _geom_attrs(collision, mesh_names, group="2"))

    for joint in children.get(link.name, []):
        child = link_by_name.get(joint.child)
        if child is not None:
            _emit_body(body_el, child, children, link_by_name, mesh_names, incoming_joint=joint)


def _joint_attrs(joint: Joint) -> dict[str, str]:
    joint_type = {
        "revolute": "hinge",
        "continuous": "hinge",
        "prismatic": "slide",
        "ball": "ball",
        "free": "free",
    }.get(joint.joint_type, "hinge")
    attrs = {"name": joint.name, "type": joint_type}
    if joint_type in {"hinge", "slide"}:
        attrs["axis"] = _numbers(joint.axis)
    if joint.limit is not None and joint.limit.lower is not None and joint.limit.upper is not None:
        attrs["limited"] = "true"
        attrs["range"] = _numbers((joint.limit.lower, joint.limit.upper))
    if joint.dynamics is not None:
        attrs["damping"] = _num(joint.dynamics.damping)
        attrs["frictionloss"] = _num(joint.dynamics.friction)
        attrs["armature"] = _num(joint.dynamics.armature)
    return attrs


def _geom_attrs(item: Visual | Collision, mesh_names: dict[str, str], **extra: str) -> dict[str, str]:
    attrs = dict(extra)
    attrs.update(_origin_attrs(item.origin))
    if item.name:
        attrs["name"] = item.name
    _apply_geometry_attrs(attrs, item.geometry, mesh_names)
    material = getattr(item, "material", None)
    if isinstance(material, str):
        attrs["material"] = material
    elif material is not None:
        attrs["material"] = material.name
    return attrs


def _apply_geometry_attrs(attrs: dict[str, str], geometry: Geometry, mesh_names: dict[str, str]) -> None:
    if isinstance(geometry, Box):
        attrs["type"] = "box"
        attrs["size"] = _numbers(tuple(v * 0.5 for v in geometry.size))
    elif isinstance(geometry, Cylinder):
        attrs["type"] = "cylinder"
        attrs["size"] = _numbers((geometry.radius, geometry.length * 0.5))
    elif isinstance(geometry, Sphere):
        attrs["type"] = "sphere"
        attrs["size"] = _num(geometry.radius)
    elif isinstance(geometry, Mesh):
        attrs["type"] = "mesh"
        attrs["mesh"] = mesh_names.get(geometry.filename, geometry.name or Path(geometry.filename).stem)
        if geometry.scale:
            attrs["scale"] = _numbers(geometry.scale)
    else:
        raise TypeError(f"unsupported geometry: {type(geometry).__name__}")


def _add_actuators(root: ET.Element, model: RobotModel) -> None:
    if not model.actuators:
        return
    actuator_el = ET.SubElement(root, "actuator")
    for actuator in model.actuators:
        tag = {
            "motor": "motor",
            "position": "position",
            "velocity": "velocity",
        }.get(actuator.actuator_type, "motor")
        attrs = {"name": actuator.name, "joint": actuator.joint, "gear": _num(actuator.gear)}
        if actuator.ctrl_range is not None:
            attrs["ctrlrange"] = _numbers(actuator.ctrl_range)
        if actuator.force_range is not None:
            attrs["forcerange"] = _numbers(actuator.force_range)
        if actuator.kp is not None and tag == "position":
            attrs["kp"] = _num(actuator.kp)
        if actuator.kv is not None and tag == "velocity":
            attrs["kv"] = _num(actuator.kv)
        ET.SubElement(actuator_el, tag, attrs)


def _add_sensors(root: ET.Element, model: RobotModel) -> None:
    if not model.sensors:
        return
    sensor_el = ET.SubElement(root, "sensor")
    for sensor in model.sensors:
        if sensor.sensor_type in {"jointpos", "jointvel"}:
            ET.SubElement(sensor_el, sensor.sensor_type, {"name": sensor.name, "joint": sensor.target})
        elif sensor.sensor_type == "imu":
            ET.SubElement(sensor_el, "framequat", {"name": sensor.name, "objtype": "body", "objname": sensor.target})
        elif sensor.sensor_type in {"force", "touch"}:
            ET.SubElement(sensor_el, sensor.sensor_type, {"name": sensor.name, "site": sensor.target})


def _origin_attrs(origin) -> dict[str, str]:
    attrs: dict[str, str] = {}
    if any(abs(v) > 0.0 for v in origin.xyz):
        attrs["pos"] = _numbers(origin.xyz)
    if any(abs(v) > 0.0 for v in origin.rpy):
        attrs["euler"] = _numbers(origin.rpy)
    return attrs


def _iter_geometries(link: Link):
    for visual in link.visuals:
        yield visual.geometry
    for collision in link.collisions:
        yield collision.geometry


def _num(value: float) -> str:
    return f"{float(value):.9g}"


def _numbers(values) -> str:
    return " ".join(_num(v) for v in values)
