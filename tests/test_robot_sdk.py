from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins.builtin.robot_sdk.main import _ISSUE_SUGGESTIONS, _TEMPLATE_FILES, RobotSdkPlugin  # noqa: E402
from robot_sdk import (  # noqa: E402
    AssetSession,
    Box,
    Cylinder,
    DHJoint,
    JointLimit,
    LegChainSpec,
    Origin,
    QuadrupedSpec,
    RobotModel,
    SerialManipulatorSpec,
    check_kinematic_spec,
    check_quadruped_spec,
    check_robot_model,
    check_serial_manipulator_spec,
    check_ur3e_like_spec,
    build_semantic_graph,
    compile_serial_manipulator,
    demo_six_dof_spec,
    demo_three_dof_spec,
    export_mjcf,
    export_urdf,
    forward_kinematics,
    identity_matrix,
    inertial_from_box,
    inertial_from_cylinder,
    matmul,
    mesh_from_vertices,
    modified_dh_transform,
    planar_two_dof_spec,
    SemanticGraphBuildError,
    standard_dh_transform,
    translation_of,
    ur3e_like_spec,
)


def _load_robot_model_from_file(path: Path) -> RobotModel:
    module_name = f"_test_robot_sdk_example_{abs(hash(path))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load example: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    builder = getattr(module, "build_robot_model", None)
    model = builder() if callable(builder) else getattr(module, "robot_model", None)
    if not isinstance(model, RobotModel):
        raise TypeError(f"example did not produce RobotModel: {path}")
    return model


def _two_link_robot() -> RobotModel:
    robot = RobotModel("test_arm")
    base_geom = Cylinder(radius=0.05, length=0.04)
    base = robot.link("base", inertial=inertial_from_cylinder(base_geom, mass=1.0))
    base.visual(base_geom)
    base.collision(base_geom)

    link_geom = Box((0.3, 0.04, 0.04))
    link = robot.link("link1", inertial=inertial_from_box(link_geom, mass=0.5))
    link.visual(link_geom, origin=Origin(xyz=(0.15, 0.0, 0.0)))
    link.collision(link_geom, origin=Origin(xyz=(0.15, 0.0, 0.0)))

    shoulder = robot.joint(
        "shoulder",
        "revolute",
        parent=base,
        child=link,
        origin=Origin(xyz=(0.0, 0.0, 0.04)),
        axis=(0.0, 0.0, 1.0),
        limit=JointLimit(lower=-1.0, upper=1.0, effort=10.0, velocity=2.0),
    )
    robot.actuator("shoulder_motor", shoulder, ctrl_range=(-1.0, 1.0))
    robot.sensor("shoulder_pos", "jointpos", "shoulder")
    return robot


def _origin_to_matrix(origin: Origin):
    roll, pitch, yaw = origin.rpy
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return (
        (cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr, origin.xyz[0]),
        (sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr, origin.xyz[1]),
        (-sp, cp * sr, cp * cr, origin.xyz[2]),
        (0.0, 0.0, 0.0, 1.0),
    )


class RobotSdkTest(unittest.TestCase):
    def test_check_robot_model_accepts_connected_robot(self) -> None:
        report = check_robot_model(_two_link_robot())
        self.assertTrue(report.ok, report.to_dict())

    def test_check_robot_model_rejects_missing_joint_limit(self) -> None:
        robot = RobotModel("bad")
        a = robot.link("a")
        b = robot.link("b")
        robot.joint("a_to_b", "revolute", parent=a, child=b)
        report = check_robot_model(robot)
        self.assertFalse(report.ok)
        self.assertIn("missing_joint_limit", {issue.code for issue in report.issues})

    def test_export_mjcf_contains_body_joint_and_actuator(self) -> None:
        xml = export_mjcf(_two_link_robot(), pretty=False)
        root = ET.fromstring(xml)
        self.assertEqual(root.tag, "mujoco")
        self.assertIsNotNone(root.find(".//body[@name='base']"))
        self.assertIsNotNone(root.find(".//joint[@name='shoulder']"))
        self.assertIsNotNone(root.find(".//actuator/motor[@name='shoulder_motor']"))
        self.assertIsNotNone(root.find(".//sensor/jointpos[@name='shoulder_pos']"))

    def test_export_urdf_contains_links_joint_and_geometry(self) -> None:
        xml = export_urdf(_two_link_robot(), pretty=False)
        root = ET.fromstring(xml)
        self.assertEqual(root.tag, "robot")
        self.assertEqual(root.attrib["name"], "test_arm")
        self.assertIsNotNone(root.find("./link[@name='base']/inertial/mass"))
        self.assertIsNotNone(root.find("./link[@name='link1']/visual/geometry/box"))
        self.assertIsNotNone(root.find("./link[@name='link1']/collision/geometry/box"))

        joint = root.find("./joint[@name='shoulder']")
        self.assertIsNotNone(joint)
        self.assertEqual(joint.attrib["type"], "revolute")  # type: ignore[union-attr]
        self.assertIsNotNone(root.find("./joint[@name='shoulder']/parent[@link='base']"))
        self.assertIsNotNone(root.find("./joint[@name='shoulder']/child[@link='link1']"))
        self.assertIsNotNone(root.find("./joint[@name='shoulder']/limit"))

    def test_robot_sdk_plugin_creates_and_compiles_template(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            plugin = RobotSdkPlugin()
            plugin.initialize({"workspace_root": td})

            created = json.loads(plugin.execute("create_robot_template", {"path": "robot_model.py"}))
            self.assertTrue(created["ok"])
            self.assertEqual(created["template"], "two_link")

            compiled = json.loads(
                plugin.execute(
                    "compile_robot_model",
                    {"path": "robot_model.py", "output_dir": "build/robot"},
                )
            )
            self.assertTrue(compiled["ok"], compiled)
            self.assertEqual(compiled["robot"], "two_link_arm")
            self.assertTrue((Path(td) / compiled["mjcf_path"]).exists())
            self.assertTrue((Path(td) / compiled["urdf_path"]).exists())
            self.assertTrue((Path(td) / compiled["report_path"]).exists())
            self.assertEqual(compiled["summary"]["status"], "success")

            probed = json.loads(plugin.execute("probe_robot_model", {"path": "robot_model.py"}))
            self.assertTrue(probed["ok"], probed)
            self.assertEqual(probed["root_links"], ["base"])
            self.assertEqual(probed["leaf_links"], ["link2"])
            self.assertEqual(len(probed["joint_chain"]), 2)
            self.assertEqual(len(probed["joints"]), 2)

    def test_robot_sdk_plugin_three_dof_template_compiles(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            plugin = RobotSdkPlugin()
            plugin.initialize({"workspace_root": td})

            created = json.loads(
                plugin.execute(
                    "create_robot_template",
                    {"path": "robot_model.py", "template": "three_dof_arm"},
                )
            )
            self.assertTrue(created["ok"], created)

            compiled = json.loads(plugin.execute("compile_robot_model", {"path": "robot_model.py"}))
            self.assertTrue(compiled["ok"], compiled)
            self.assertEqual(compiled["robot"], "three_dof_arm")
            self.assertEqual(len([name for name in compiled["joints"] if not name.endswith("_to_tool0")]), 3)

            model_path = Path(td) / created["path"]
            text = model_path.read_text(encoding="utf-8")
            self.assertIn("compile_serial_manipulator", text)
            self.assertIn("demo_three_dof_spec", text)

    def test_robot_sdk_plugin_ur3e_like_template_compiles(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            plugin = RobotSdkPlugin()
            plugin.initialize({"workspace_root": td})

            created = json.loads(
                plugin.execute(
                    "create_robot_template",
                    {"path": "robot_model.py", "template": "ur3e_like"},
                )
            )
            self.assertTrue(created["ok"], created)
            self.assertIn("ur3e_like", created["available_templates"])

            compiled = json.loads(plugin.execute("compile_robot_model", {"path": "robot_model.py"}))

            self.assertTrue(compiled["ok"], compiled)
            self.assertEqual(compiled["robot"], "ur3e_like")
            self.assertEqual(len([name for name in compiled["joints"] if not name.endswith("_to_tool0")]), 6)
            self.assertTrue((Path(td) / compiled["mjcf_path"]).exists())
            self.assertTrue((Path(td) / compiled["urdf_path"]).exists())

    def test_primitive_three_dof_template_is_backup_only(self) -> None:
        backup = PROJECT_ROOT / "plugins" / "builtin" / "robot_sdk" / "templates" / "_backup" / "three_dof_arm_primitive.py"
        self.assertTrue(backup.exists(), backup)

        model = _load_robot_model_from_file(backup)
        report = check_robot_model(model)

        self.assertTrue(report.ok, report.to_dict())
        self.assertEqual(model.name, "three_dof_arm_primitive")
        self.assertEqual(len(model.joints), 3)

        with tempfile.TemporaryDirectory() as td:
            plugin = RobotSdkPlugin()
            plugin.initialize({"workspace_root": td})
            created = json.loads(plugin.execute("create_robot_template", {"path": "robot_model.py"}))

        self.assertNotIn("three_dof_arm_primitive", created["available_templates"])

    def test_robot_sdk_plugin_returns_fix_suggestions_for_invalid_model(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "robot_model.py").write_text(
                "\n".join(
                    [
                        "from robot_sdk import RobotModel",
                        "def build_robot_model():",
                        "    robot = RobotModel('bad_arm')",
                        "    a = robot.link('a')",
                        "    b = robot.link('b')",
                        "    robot.joint('a_to_b', 'revolute', parent=a, child=b)",
                        "    return robot",
                        "robot_model = build_robot_model()",
                    ]
                ),
                encoding="utf-8",
            )
            plugin = RobotSdkPlugin()
            plugin.initialize({"workspace_root": td})

            compiled = json.loads(plugin.execute("compile_robot_model", {"path": "robot_model.py"}))
            self.assertFalse(compiled["ok"])
            self.assertIsNone(compiled["mjcf_path"])
            self.assertIsNone(compiled["urdf_path"])
            self.assertGreater(len(compiled["summary"]["blocking_errors"]), 0)
            self.assertGreater(len(compiled["summary"]["next_actions"]), 0)
            suggestion_codes = {item["code"] for item in compiled["summary"]["suggestions"]}
            self.assertIn("missing_joint_limit", suggestion_codes)

    def test_mesh_assets_export_to_mjcf_and_urdf(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            session = AssetSession(td)
            mesh_export = mesh_from_vertices(
                vertices=[(0.0, 0.0, 0.0), (0.1, 0.0, 0.0), (0.0, 0.1, 0.0)],
                faces=[(0, 1, 2)],
                name="triangle_link",
                assets=session,
            )
            self.assertTrue(Path(mesh_export.mesh.materialized_path).exists())  # type: ignore[arg-type]

            robot = RobotModel("mesh_robot")
            geom = Box((0.1, 0.1, 0.01))
            link = robot.link("base", inertial=inertial_from_box(geom, mass=0.1))
            link.visual(mesh_export.mesh)
            link.collision(geom)

            mjcf = ET.fromstring(export_mjcf(robot, pretty=False))
            self.assertIsNotNone(mjcf.find(".//mesh[@file='assets/meshes/triangle_link.obj']"))
            self.assertIsNotNone(mjcf.find(".//geom[@type='mesh']"))

            urdf = ET.fromstring(export_urdf(robot, pretty=False))
            self.assertIsNotNone(urdf.find(".//mesh[@filename='assets/meshes/triangle_link.obj']"))

    def test_robot_sdk_plugin_reports_generated_obj_paths(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "robot_model.py").write_text(
                "\n".join(
                    [
                        "from robot_sdk import AssetSession, RobotModel, mesh_from_vertices",
                        "def build_robot_model():",
                        "    assets = AssetSession('build/robot')",
                        "    mesh_export = mesh_from_vertices(",
                        "        vertices=[(0, 0, 0), (0.1, 0, 0), (0, 0.1, 0)],",
                        "        faces=[(0, 1, 2)],",
                        "        name='panel',",
                        "        assets=assets,",
                        "    )",
                        "    robot = RobotModel('obj_robot')",
                        "    base = robot.link('base')",
                        "    base.visual(mesh_export.mesh)",
                        "    return robot",
                        "robot_model = build_robot_model()",
                    ]
                ),
                encoding="utf-8",
            )
            plugin = RobotSdkPlugin()
            plugin.initialize({"workspace_root": td})

            compiled = json.loads(plugin.execute("compile_robot_model", {"path": "robot_model.py"}))

            self.assertTrue(compiled["ok"], compiled)
            self.assertEqual(compiled["obj_paths"], ["build/robot/assets/meshes/panel.obj"])
            self.assertTrue((root / compiled["obj_paths"][0]).exists())

    def test_robot_sdk_docs_are_present(self) -> None:
        docs = PROJECT_ROOT / "robot_sdk" / "docs"
        for name in ["concepts.md", "robot_arms.md", "assets.md", "export.md", "troubleshooting.md"]:
            path = docs / name
            self.assertTrue(path.exists(), path)
            self.assertGreater(len(path.read_text(encoding="utf-8")), 200)

    def test_robot_sdk_examples_load_and_compile(self) -> None:
        examples = PROJECT_ROOT / "examples" / "robot_sdk"
        old_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as td:
            try:
                os.chdir(td)
                for path in sorted(examples.glob("*.py")):
                    with self.subTest(example=path.name):
                        model = _load_robot_model_from_file(path)
                        report = check_robot_model(model)
                        self.assertTrue(report.ok, report.to_dict())
                        self.assertIn("<mujoco", export_mjcf(model, pretty=False))
                        self.assertIn("<robot", export_urdf(model, pretty=False))
            finally:
                os.chdir(old_cwd)

    def test_robot_sdk_plugin_all_templates_compile(self) -> None:
        for template in ["empty", "two_link", "three_dof_arm", "ur3e_like"]:
            with self.subTest(template=template), tempfile.TemporaryDirectory() as td:
                plugin = RobotSdkPlugin()
                plugin.initialize({"workspace_root": td})
                created = json.loads(
                    plugin.execute(
                        "create_robot_template",
                        {"path": "robot_model.py", "template": template},
                    )
                )
                self.assertTrue(created["ok"], created)
                compiled = json.loads(plugin.execute("compile_robot_model", {"path": "robot_model.py"}))
                self.assertTrue(compiled["ok"], compiled)
                self.assertGreater(len(compiled["summary"]["next_actions"]), 0)

    def test_robot_sdk_template_registry_matches_manifest_enum(self) -> None:
        manifest = PROJECT_ROOT / "plugins" / "builtin" / "robot_sdk" / "plugin.yaml"
        text = manifest.read_text(encoding="utf-8")

        self.assertIn(f"enum: [{', '.join(_TEMPLATE_FILES)}]", text)

    def test_robot_sdk_plugin_has_ur3e_fix_suggestions(self) -> None:
        for code in [
            "ur3e_revolute_count",
            "ur3e_missing_wrist_axes",
            "ur3e_missing_axis_hint",
            "ur3e_missing_tool0",
        ]:
            with self.subTest(code=code):
                self.assertIn(code, _ISSUE_SUGGESTIONS)


class RobotSdkKinematicsTest(unittest.TestCase):
    def assertVecAlmostEqual(self, actual, expected, places: int = 7) -> None:  # noqa: N802
        self.assertEqual(len(actual), len(expected))
        for got, want in zip(actual, expected):
            self.assertAlmostEqual(got, want, places=places)

    def test_standard_dh_transform_places_link_along_x(self) -> None:
        transform = standard_dh_transform(alpha=0.0, a=0.3, d=0.1, theta=0.0)
        self.assertVecAlmostEqual(translation_of(transform), (0.3, 0.0, 0.1))

    def test_modified_dh_transform_places_link_along_x(self) -> None:
        transform = modified_dh_transform(alpha=0.0, a=0.3, d=0.1, theta=0.0)
        self.assertVecAlmostEqual(translation_of(transform), (0.3, 0.0, 0.1))

    def test_planar_two_dof_forward_kinematics_at_zero_pose(self) -> None:
        spec = planar_two_dof_spec(link1=0.3, link2=0.2)
        report = check_kinematic_spec(spec)
        self.assertTrue(report.ok, report.to_dict())

        transform = forward_kinematics(spec, {"q1": 0.0, "q2": 0.0})

        self.assertVecAlmostEqual(translation_of(transform), (0.5, 0.0, 0.0))

    def test_planar_two_dof_forward_kinematics_uses_joint_values(self) -> None:
        spec = planar_two_dof_spec(link1=0.3, link2=0.2)
        transform = forward_kinematics(spec, {"q1": math.pi / 2.0, "q2": 0.0})

        self.assertVecAlmostEqual(translation_of(transform), (0.0, 0.5, 0.0))

    def test_three_dof_builder_has_valid_dof_and_fk(self) -> None:
        spec = demo_three_dof_spec()
        report = check_serial_manipulator_spec(spec)
        self.assertTrue(report.ok, report.to_dict())
        self.assertEqual(spec.dof, 3)

        transform = forward_kinematics(spec, [0.0, 0.0, 0.0])

        self.assertVecAlmostEqual(translation_of(transform), (0.58, 0.0, 0.08))

    def test_six_dof_builder_has_six_moving_joints(self) -> None:
        spec = demo_six_dof_spec()
        report = check_serial_manipulator_spec(spec)
        self.assertTrue(report.ok, report.to_dict())
        self.assertEqual(spec.dof, 6)
        self.assertEqual(len([joint for joint in spec.joints if joint.joint_type != "fixed"]), 6)

        transform = forward_kinematics(spec, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0])

        self.assertEqual(len(transform), 4)
        self.assertTrue(all(len(row) == 4 for row in transform))

    def test_ur3e_like_builder_matches_template_contract(self) -> None:
        spec = ur3e_like_spec()

        report = check_ur3e_like_spec(spec)
        self.assertTrue(report.ok, report.to_dict())
        self.assertEqual(spec.name, "ur3e_like")
        self.assertEqual(spec.dof, 6)
        self.assertEqual(spec.base_frame, "base_link")
        self.assertEqual(spec.tool_frame, "tool0")
        self.assertEqual([joint.joint_type for joint in spec.joints], ["revolute"] * 6)
        self.assertEqual(
            [joint.name for joint in spec.joints],
            ["shoulder", "upper_arm", "forearm", "wrist_1", "wrist_2", "wrist_3"],
        )
        self.assertEqual(
            {joint.role for joint in spec.joints},
            {"shoulder_yaw", "shoulder_pitch", "elbow_pitch", "wrist_1_pitch", "wrist_2_yaw", "wrist_3_roll"},
        )

    def test_ur3e_like_forward_kinematics_at_zero_pose(self) -> None:
        spec = ur3e_like_spec()

        transform = forward_kinematics(spec, {f"q{i}": 0.0 for i in range(1, 7)})

        self.assertVecAlmostEqual(translation_of(transform), (-0.45675, -0.3293, -0.13105))

    def test_ur3e_like_checker_reports_contract_errors(self) -> None:
        spec = ur3e_like_spec()

        five_joint_spec = SerialManipulatorSpec(
            name=spec.name,
            dof=5,
            representation=spec.representation,
            base_frame=spec.base_frame,
            tool_frame=spec.tool_frame,
            joints=spec.joints[:5],
        )
        bad_tool_spec = SerialManipulatorSpec(
            name=spec.name,
            dof=spec.dof,
            representation=spec.representation,
            base_frame=spec.base_frame,
            tool_frame="flange",
            joints=spec.joints,
        )
        missing_wrist_role = DHJoint(
            "wrist_2",
            alpha=-math.pi / 2,
            a=0.0,
            d=0.08535,
            theta="q5",
            limit=(-2 * math.pi, 2 * math.pi),
            role="wrist_2",
            axis_hint=(0.0, 0.0, 1.0),
        )
        bad_role_spec = SerialManipulatorSpec(
            name=spec.name,
            dof=spec.dof,
            representation=spec.representation,
            base_frame=spec.base_frame,
            tool_frame=spec.tool_frame,
            joints=[*spec.joints[:4], missing_wrist_role, spec.joints[5]],
        )
        missing_axis_hint = DHJoint(
            "shoulder",
            alpha=math.pi / 2,
            a=0.0,
            d=0.15185,
            theta="q1",
            limit=(-2 * math.pi, 2 * math.pi),
            role="shoulder_yaw",
        )
        bad_axis_spec = SerialManipulatorSpec(
            name=spec.name,
            dof=spec.dof,
            representation=spec.representation,
            base_frame=spec.base_frame,
            tool_frame=spec.tool_frame,
            joints=[missing_axis_hint, *spec.joints[1:]],
        )

        cases = [
            (five_joint_spec, "ur3e_revolute_count"),
            (bad_tool_spec, "ur3e_missing_tool0"),
            (bad_role_spec, "ur3e_missing_wrist_axes"),
            (bad_axis_spec, "ur3e_missing_axis_hint"),
        ]
        for bad_spec, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                report = check_ur3e_like_spec(bad_spec)
                self.assertFalse(report.ok)
                self.assertIn(expected_code, {issue.code for issue in report.issues})

    def test_ur3e_like_compiles_expected_links_and_axes(self) -> None:
        robot = compile_serial_manipulator(ur3e_like_spec())
        report = check_robot_model(robot)

        self.assertTrue(report.ok, report.to_dict())
        self.assertEqual(
            [link.name for link in robot.links],
            ["base_link", "shoulder_link", "upper_arm_link", "forearm_link", "wrist_1_link", "wrist_2_link", "wrist_3_link", "tool0"],
        )
        self.assertEqual(len([joint for joint in robot.joints if joint.joint_type == "revolute"]), 6)
        self.assertEqual(robot.get_joint("shoulder").axis, (0.0, 0.0, 1.0))
        self.assertEqual(robot.get_joint("upper_arm").axis, (0.0, 1.0, 0.0))
        self.assertEqual(robot.get_joint("forearm").axis, (0.0, 1.0, 0.0))
        self.assertEqual(robot.get_joint("wrist_1").axis, (0.0, 1.0, 0.0))
        self.assertEqual(robot.get_joint("wrist_2").axis, (0.0, 0.0, 1.0))
        self.assertEqual(robot.get_joint("wrist_3").axis, (0.0, 1.0, 0.0))
        self.assertEqual({actuator.ctrl_range for actuator in robot.actuators}, {(-1.0, 1.0)})

    def test_serial_spec_reports_dof_mismatch(self) -> None:
        spec = SerialManipulatorSpec(
            name="bad_dof",
            dof=2,
            representation="dh",
            joints=[DHJoint("only_joint", alpha=0.0, a=0.1, d=0.0, theta="q1", limit=(-1.0, 1.0))],
        )

        report = check_serial_manipulator_spec(spec)

        self.assertFalse(report.ok)
        self.assertIn("dof_mismatch", {issue.code for issue in report.issues})

    def test_forward_kinematics_requires_values_for_variables(self) -> None:
        spec = planar_two_dof_spec()

        with self.assertRaisesRegex(ValueError, "missing joint value"):
            forward_kinematics(spec, {"q1": 0.0})

    def test_forward_kinematics_requires_mapping_for_two_variables_in_one_row(self) -> None:
        spec = SerialManipulatorSpec(
            name="coupled_row",
            dof=1,
            representation="dh",
            joints=[DHJoint("joint", alpha=0.0, a=0.0, d="d1", theta="q1", limit=(-1.0, 1.0))],
        )

        with self.assertRaisesRegex(ValueError, "use a mapping"):
            forward_kinematics(spec, [0.0])

    def test_serial_spec_rejects_unimplemented_poe_representation(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported serial_spec.representation"):
            SerialManipulatorSpec(
                name="poe_arm",
                dof=1,
                representation="poe",  # type: ignore[arg-type]
                joints=[DHJoint("joint", alpha=0.0, a=0.0, d=0.0, theta="q1", limit=(-1.0, 1.0))],
            )

    def test_quadruped_spec_accepts_four_leg_layout(self) -> None:
        leg_joints = [
            DHJoint("hip_roll", alpha=0.0, a=0.0, d=0.0, theta="q1", limit=(-0.8, 0.8)),
            DHJoint("hip_pitch", alpha=0.0, a=0.18, d=0.0, theta="q2", limit=(-1.8, 1.8)),
            DHJoint("knee_pitch", alpha=0.0, a=0.18, d=0.0, theta="q3", limit=(-2.4, 0.0)),
        ]
        spec = QuadrupedSpec(
            name="basic_quadruped",
            body_size=(0.5, 0.18, 0.12),
            legs=[
                LegChainSpec("front_left", mount_xyz=(0.2, 0.09, 0.0), joints=leg_joints, segment_lengths=(0.18, 0.18)),
                LegChainSpec(
                    "front_right",
                    mount_xyz=(0.2, -0.09, 0.0),
                    joints=leg_joints,
                    segment_lengths=(0.18, 0.18),
                    mirror_of="front_left",
                ),
                LegChainSpec("rear_left", mount_xyz=(-0.2, 0.09, 0.0), joints=leg_joints, segment_lengths=(0.18, 0.18)),
                LegChainSpec(
                    "rear_right",
                    mount_xyz=(-0.2, -0.09, 0.0),
                    joints=leg_joints,
                    segment_lengths=(0.18, 0.18),
                    mirror_of="rear_left",
                ),
            ],
        )

        report = check_quadruped_spec(spec)

        self.assertTrue(report.ok, report.to_dict())

    def test_quadruped_spec_requires_four_legs(self) -> None:
        spec = QuadrupedSpec(name="bad_quadruped", body_size=(0.5, 0.18, 0.12), legs=[])

        report = check_quadruped_spec(spec)

        self.assertFalse(report.ok)
        self.assertIn("quadruped_leg_count", {issue.code for issue in report.issues})

    def test_compile_serial_manipulator_creates_valid_robot_model(self) -> None:
        spec = planar_two_dof_spec(link1=0.3, link2=0.2)

        robot = compile_serial_manipulator(spec)
        report = check_robot_model(robot)

        self.assertTrue(report.ok, report.to_dict())
        self.assertEqual(robot.meta["kinematic_type"], "serial_manipulator")
        self.assertEqual(robot.meta["representation"], "dh")
        self.assertEqual(len([joint for joint in robot.joints if joint.joint_type != "fixed"]), 2)
        self.assertEqual(len(robot.actuators), 2)
        self.assertEqual(len(robot.sensors), 2)
        self.assertEqual(robot.links[0].name, "base")
        self.assertEqual(robot.links[-1].name, "tool0")

    def test_compile_serial_manipulator_preserves_dh_metadata(self) -> None:
        spec = demo_three_dof_spec()

        robot = compile_serial_manipulator(spec)

        shoulder = robot.get_joint("shoulder_yaw")
        self.assertEqual(shoulder.meta["source"], "kinematics")
        self.assertEqual(shoulder.meta["dh_index"], 0)
        self.assertEqual(shoulder.meta["dh"]["theta"], "q1")
        self.assertEqual(shoulder.axis, (0.0, 0.0, 1.0))
        self.assertIsNotNone(shoulder.limit)

    def test_compile_serial_manipulator_uses_normalized_actuator_control_range(self) -> None:
        robot = compile_serial_manipulator(demo_three_dof_spec())

        self.assertTrue(robot.actuators)
        self.assertEqual({actuator.ctrl_range for actuator in robot.actuators}, {(-1.0, 1.0)})

    def test_compiled_joint_origins_match_zero_pose_forward_kinematics(self) -> None:
        spec = demo_three_dof_spec()
        robot = compile_serial_manipulator(spec)
        transform = identity_matrix()
        for joint in robot.joints:
            if joint.meta.get("role") == "tool_mount":
                continue
            transform = matmul(transform, _origin_to_matrix(joint.origin))

        expected = forward_kinematics(spec, [0.0, 0.0, 0.0])
        self.assertVecAlmostEqual(translation_of(transform), translation_of(expected))

    def test_compile_serial_manipulator_exports_to_mjcf_and_urdf(self) -> None:
        spec = demo_six_dof_spec()

        robot = compile_serial_manipulator(spec)

        self.assertIn("<mujoco", export_mjcf(robot, pretty=False))
        self.assertIn("<robot", export_urdf(robot, pretty=False))

    def test_build_semantic_graph_creates_base_rooted_chain(self) -> None:
        spec = demo_three_dof_spec()
        robot = compile_serial_manipulator(spec)

        graph = build_semantic_graph(robot, spec=spec)

        self.assertEqual(graph.root, "base")
        self.assertEqual(graph.path_to("tool0"), ["base", "shoulder_yaw_link", "shoulder_pitch_link", "wrist_pitch_link", "tool0"])
        self.assertEqual(graph.children("base"), ["shoulder_yaw_link"])
        self.assertEqual(graph.get("shoulder_pitch_link").parent, "shoulder_yaw_link")
        self.assertEqual(graph.get("shoulder_pitch_link").incoming_joint, "shoulder_pitch")
        self.assertEqual(graph.get("shoulder_pitch_link").dh_index, 1)
        self.assertEqual(graph.get("shoulder_pitch_link").role, "upper_arm")

    def test_build_semantic_graph_records_spans_axes_and_metadata(self) -> None:
        spec = demo_three_dof_spec()
        robot = compile_serial_manipulator(spec)

        graph = build_semantic_graph(robot, spec=spec)
        first_edge = graph.edge("base", "shoulder_yaw_link")
        second_edge = graph.edge("shoulder_yaw_link", "shoulder_pitch_link")

        self.assertVecAlmostEqual(first_edge.span, (0.0, 0.0, 0.08))
        self.assertAlmostEqual(first_edge.span_length, 0.08)
        self.assertEqual(first_edge.axis, (0.0, 0.0, 1.0))
        self.assertVecAlmostEqual(second_edge.span, (0.32, 0.0, 0.0))
        self.assertEqual(graph.get("shoulder_pitch_link").metadata["joint"]["dh"]["theta"], "q2")
        self.assertEqual(graph.get("shoulder_pitch_link").metadata["kinematic_joint"]["role"], "upper_arm")

    def test_semantic_graph_frame_matches_zero_pose_forward_kinematics(self) -> None:
        spec = demo_three_dof_spec()
        robot = compile_serial_manipulator(spec)

        graph = build_semantic_graph(robot, spec=spec)
        wrist_node = graph.get("wrist_pitch_link")
        expected = forward_kinematics(spec, [0.0, 0.0, 0.0])

        self.assertVecAlmostEqual(wrist_node.frame.xyz, translation_of(expected))

    def test_semantic_graph_supports_prismatic_joint(self) -> None:
        robot = RobotModel("prismatic_graph")
        base = robot.link("base")
        slider = robot.link("slider")
        robot.joint(
            "slide_z",
            "prismatic",
            parent=base,
            child=slider,
            origin=Origin(xyz=(0.0, 0.0, 0.2)),
            axis=(0.0, 0.0, 1.0),
            limit=JointLimit(lower=0.0, upper=0.1),
        )

        graph = build_semantic_graph(robot)

        self.assertEqual(graph.root, "base")
        self.assertEqual(graph.edge("base", "slider").kind, "joint")
        self.assertEqual(graph.get("slider").axis, (0.0, 0.0, 1.0))
        self.assertVecAlmostEqual(graph.get("slider").frame.xyz, (0.0, 0.0, 0.2))

    def test_semantic_graph_preserves_nonzero_rpy_origin(self) -> None:
        robot = RobotModel("rpy_graph")
        base = robot.link("base")
        elbow = robot.link("elbow")
        robot.joint(
            "base_to_elbow",
            "fixed",
            parent=base,
            child=elbow,
            origin=Origin(xyz=(0.1, 0.0, 0.0), rpy=(0.0, math.pi / 2.0, 0.0)),
        )

        graph = build_semantic_graph(robot)

        self.assertVecAlmostEqual(graph.get("elbow").frame.xyz, (0.1, 0.0, 0.0))
        self.assertVecAlmostEqual(graph.get("elbow").frame.rpy, (0.0, math.pi / 2.0, 0.0))

    def test_semantic_graph_without_spec_still_uses_robot_metadata(self) -> None:
        robot = compile_serial_manipulator(demo_three_dof_spec())

        graph = build_semantic_graph(robot)

        self.assertEqual(graph.root, "base")
        self.assertEqual(graph.metadata["kinematic_spec"], None)
        self.assertEqual(graph.get("shoulder_pitch_link").role, "upper_arm")
        self.assertEqual(graph.get("shoulder_pitch_link").dh_index, 1)

    def test_ur3e_like_semantic_graph_matches_robot_model_chain(self) -> None:
        spec = ur3e_like_spec()
        robot = compile_serial_manipulator(spec)

        graph = build_semantic_graph(robot, spec=spec)

        self.assertEqual(graph.root, "base_link")
        self.assertEqual(graph.order, [link.name for link in robot.links])
        self.assertEqual(graph.path_to("tool0")[-3:], ["wrist_2_link", "wrist_3_link", "tool0"])
        self.assertEqual(graph.get("wrist_2_link").axis, (0.0, 0.0, 1.0))
        self.assertEqual(graph.get("wrist_2_link").dh_index, 4)
        self.assertAlmostEqual(graph.get("shoulder_link").axis_angle_to_children["upper_arm_link"], math.pi / 2.0)
        self.assertEqual(graph.edge("wrist_3_link", "tool0").kind, "fixed")

    def test_build_semantic_graph_rejects_multiple_roots(self) -> None:
        robot = RobotModel("bad_graph")
        robot.link("base_a")
        robot.link("base_b")

        with self.assertRaisesRegex(SemanticGraphBuildError, "exactly one root"):
            build_semantic_graph(robot)


if __name__ == "__main__":
    unittest.main()
