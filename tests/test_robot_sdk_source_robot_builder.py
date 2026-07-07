import tempfile
import unittest
from pathlib import Path

from robot_sdk.cad.source_assembly import export_source_step
from robot_sdk.cad.source_export import (
    SourceSubassemblyExportSpec,
    export_source_robot_step_package,
)
from robot_sdk.cad.source_robot_builder import (
    build_simple_source_serial_robot,
    build_source_robot_from_layout,
)
from robot_sdk.layout import build_mechanical_layout_from_structure_plan
from robot_sdk.structure import build_generic_6axis_cobot_structure_plan
from robot_sdk.types import DHParam, JointSpec, KinematicModel, LinkSpec


class RobotSdkSourceRobotBuilderTest(unittest.TestCase):
    def test_builds_simple_2dof_source_robot_chain(self) -> None:
        result = build_simple_source_serial_robot(
            joint_count=2,
            link_lengths=[80.0, 60.0],
            name="two_dof_source_robot",
        )

        self.assertEqual(
            result.part_ids,
            ["base", "J1", "L1", "J2", "L2", "end_effector"],
        )
        self.assertEqual(len(result.relations), 5)
        self.assertEqual(result.metadata["production_assembly_source"], "source_joint")
        self.assertEqual(result.metadata["assembly_backend"], "build123d")
        self.assertEqual(result.metadata["link_lengths"], [80.0, 60.0])
        self.assertEqual(len(result.assembly.assembly_mates), 5)

        base = result.part_catalog.require("base").solid
        tool = result.part_catalog.require("end_effector").solid
        self.assertGreater(float(tool.center().X), float(base.center().X))
        self.assertAlmostEqual(float(result.part_catalog.require("J1").solid.center().Z), 5.0)

    def test_exports_simple_source_robot_step(self) -> None:
        result = build_simple_source_serial_robot(joint_count=2)
        output_path = Path(tempfile.gettempdir()) / "cathy_source_robot_builder.step"

        export_result = export_source_step(result.assembly, output_path)

        self.assertTrue(export_result.exists)
        self.assertGreater(export_result.size_bytes or 0, 0)

    def test_exports_source_step_package(self) -> None:
        result = build_simple_source_serial_robot(joint_count=2)
        with tempfile.TemporaryDirectory() as temp_dir:
            package = export_source_robot_step_package(
                result,
                temp_dir,
                whole_machine_filename="source_robot.step",
                subassemblies=[
                    SourceSubassemblyExportSpec(
                        name="joint1",
                        part_ids=["J1"],
                        part_export_names={"J1": "joint1_housing"},
                    ),
                    SourceSubassemblyExportSpec(
                        name="link1",
                        part_ids=["L1"],
                        part_export_names={"L1": "link1_body"},
                    ),
                ],
            )

            self.assertTrue((Path(temp_dir) / "source_robot.step").exists())
            self.assertTrue((Path(temp_dir) / "joint1" / "joint1_housing.step").exists())
            self.assertTrue((Path(temp_dir) / "joint1" / "joint1.step").exists())
            self.assertTrue((Path(temp_dir) / "link1" / "link1_body.step").exists())
            self.assertTrue((Path(temp_dir) / "link1" / "link1.step").exists())
            self.assertEqual(package.file_count, 5)
            self.assertEqual(package.whole_machine_assembly_source, "source_joint")
            self.assertIsNotNone(package.whole_machine_export.bbox)
            self.assertTrue(package.whole_machine_export.bbox.valid)
            self.assertTrue(
                all(item.assembly_export.bbox and item.assembly_export.bbox.valid for item in package.subassemblies)
            )

    def test_builds_structure_aware_source_robot_from_layout(self) -> None:
        layout = build_mechanical_layout_from_structure_plan(
            _planar_6dof_model(),
            build_generic_6axis_cobot_structure_plan(reach_mm=500),
        )

        result = build_source_robot_from_layout(layout, name="layout_source_robot")

        self.assertEqual(len(result.part_ids), 14)
        self.assertEqual(len(result.relations), 13)
        self.assertEqual(result.metadata["source"], "source_robot_builder_from_layout")
        self.assertEqual(result.metadata["layout_template"], "structure_plan")
        routes = {item["link_id"]: item for item in result.metadata["link_routes"]}
        self.assertEqual(routes["L1"]["route_vector"], (0.0, 0.0, 90.0))
        self.assertEqual(routes["L1"]["route_type"], "offset")
        self.assertEqual(routes["L3"]["route_type"], "elbow")
        self.assertEqual(routes["L2"]["route_vector"], (210.0, 0.0, 60.0))
        self.assertEqual(routes["L3"]["route_vector"], (170.0, 0.0, -30.0))
        self.assertGreater(routes["L2"]["route_vector"][2], 0.0)
        self.assertLess(routes["L3"]["route_vector"][2], 0.0)

        base = result.part_catalog.require("base").solid
        tool = result.part_catalog.require("end_effector").solid
        self.assertGreater(float(tool.center().X), float(base.center().X))
        self.assertGreater(float(tool.center().Z), float(base.center().Z))

    def test_rejects_invalid_chain_inputs(self) -> None:
        with self.assertRaises(ValueError):
            build_simple_source_serial_robot(joint_count=0)
        with self.assertRaises(ValueError):
            build_simple_source_serial_robot(joint_count=2, link_lengths=[100.0])
        with self.assertRaises(ValueError):
            build_simple_source_serial_robot(joint_count=1, link_lengths=[0.0])


def _planar_6dof_model() -> KinematicModel:
    joints = []
    links = []
    dh_params = []
    previous_link = "base"
    for index in range(1, 7):
        joint_id = f"J{index}"
        link_id = f"L{index}"
        joints.append(
            JointSpec(
                id=joint_id,
                type="revolute",
                parent_link=previous_link,
                child_link=link_id,
            )
        )
        links.append(LinkSpec(id=link_id, length=80.0, parent_joint=joint_id))
        dh_params.append(
            DHParam(
                joint_id=joint_id,
                a=80.0,
                alpha=0.0,
                d=0.0,
                theta=0.0,
                variable=f"theta{index}",
            )
        )
        previous_link = link_id
    return KinematicModel(convention="dh", joints=joints, links=links, dh_params=dh_params)


if __name__ == "__main__":
    unittest.main()
