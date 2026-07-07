import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from robot_sdk.cad.cq_assembly import build_cadquery_assembly
from robot_sdk.cad.export import (
    CadQuerySubassemblyExportSpec,
    export_robot_step_package,
    export_step,
)
from robot_sdk.layout.mechanical_layout import build_tabletop_serial_mechanical_layout
from robot_sdk.types import DHParam

from tests.test_robot_sdk_cq_parts import FakeCadQuery, FakeWorkplane


class FakeAssembly:
    def __init__(self) -> None:
        self.parts: list[tuple[str, FakeWorkplane, object | None]] = []
        self.constraints: list[tuple] = []
        self.solve_calls: list[int] = []
        self.saved: list[dict] = []

    def add(self, obj, name: str, loc=None):
        self.parts.append((name, obj, loc))
        return self

    def constrain(self, *args):
        self.constraints.append(args)
        return self

    def solve(self, verbosity: int = 0):
        self.solve_calls.append(verbosity)
        return self

    def save(self, path: str, **kwargs):
        self.saved.append({"path": path, **kwargs})
        Path(path).write_text("STEP", encoding="utf-8")
        return self


class FailingAssembly(FakeAssembly):
    def solve(self, verbosity: int = 0):
        super().solve(verbosity)
        raise RuntimeError("solve failed")


class FakeCadQueryWithAssembly(FakeCadQuery):
    Assembly = FakeAssembly


class FailingCadQueryWithAssembly(FakeCadQuery):
    Assembly = FailingAssembly


class RobotSdkCadQueryAssemblyTest(unittest.TestCase):
    def _layout(self):
        return build_tabletop_serial_mechanical_layout(
            [
                DHParam(joint_id="J1", a=120, alpha=0, d=0, theta=0),
                DHParam(joint_id="J2", a=100, alpha=0, d=0, theta=0),
            ]
        )

    def test_builds_constraint_solved_cadquery_assembly(self) -> None:
        result = build_cadquery_assembly(
            self._layout(),
            cq_module=FakeCadQueryWithAssembly,
        )

        self.assertTrue(result.solved)
        self.assertIsNone(result.solve_error)
        self.assertEqual(result.part_count, 6)
        self.assertEqual(result.constraint_count, 11)
        self.assertEqual([name for name, _obj, _loc in result.assembly.parts], result.part_catalog.part_ids())
        self.assertEqual(len(result.assembly.constraints), 11)
        self.assertEqual(result.assembly.constraints[0], ("base", "Fixed"))
        self.assertEqual(result.assembly.solve_calls, [0])
        self.assertEqual(result.metadata["assembly_mode"], "constraint_solve")
        self.assertEqual(result.metadata["placement_mode"], "full_semantic_solve")
        self.assertEqual(result.metadata["solver_constraint_mode"], "semantic_mate_constraints")
        self.assertEqual(result.metadata["constraint_scope"], "full_assembly_semantic_constraints")
        self.assertEqual(result.metadata["semantic_constraint_count"], 10)
        self.assertEqual(result.metadata["unsafe_mate_constraint_count"], 0)
        self.assertEqual(
            result.metadata["semantic_constraints_applied_to_solver"],
            "full_assembly",
        )
        self.assertEqual(result.metadata["semantic_constraint_application"], "full_assembly")
        self.assertEqual(result.metadata["production_assembly_source"], "full_semantic_solve")
        self.assertTrue(result.metadata["production_full_semantic_solve_used"])
        self.assertEqual(result.metadata["local_subassembly_count"], 3)
        self.assertEqual(result.metadata["local_subassembly_solved_count"], 3)
        self.assertEqual(result.metadata["local_subassembly_failed_count"], 0)
        self.assertIsNotNone(result.full_assembly_fixture_result)
        self.assertTrue(result.metadata["full_assembly_fixture_requested"])
        self.assertTrue(result.metadata["full_assembly_fixture_ran"])
        self.assertTrue(result.metadata["full_assembly_fixture_solved"])
        self.assertEqual(result.metadata["full_assembly_fixture_gate_status"], "clear_for_fixture")
        self.assertEqual(result.metadata["full_assembly_fixture_constraint_count"], 11)
        self.assertEqual(result.metadata["full_assembly_fixture_semantic_constraint_count"], 10)
        self.assertEqual(len(result.metadata["full_assembly_fixture_residual_reports"]), 10)
        self.assertEqual(
            result.metadata["full_assembly_fixture"]["semantic_constraint_application"],
            "full_assembly_fixture",
        )
        self.assertEqual(
            result.metadata["local_subassembly_names"],
            ["J2_L2_end_effector", "J1_L1_J2", "base_J1_mount"],
        )
        self.assertEqual(result.metadata["local_subassemblies"][1]["role"], "joint_link_joint")
        self.assertEqual(result.metadata["local_subassemblies"][1]["spec_metadata"], {"role": "joint_link_joint"})
        self.assertEqual(result.metadata["local_subassemblies"][2]["role"], "base_mount")
        self.assertEqual(result.metadata["local_subassemblies"][2]["source"], "sdk_promoted_fixture")
        self.assertEqual(len(result.local_subassembly_results), 3)
        self.assertEqual(
            result.metadata["local_subassemblies"][0]["applied_constraint_ids"],
            [
                "J2_output_to_L2_input_plane",
                "J2_output_tangent_to_L2_input_tangent_axis",
                "L2_output_to_end_effector_mount_plane",
                "L2_output_tangent_to_end_effector_mount_tangent_axis",
            ],
        )
        self.assertIsNotNone(result.assembly.parts[0][2])

    def test_can_use_fixed_layout_pose_as_explicit_fallback(self) -> None:
        result = build_cadquery_assembly(
            self._layout(),
            cq_module=FakeCadQueryWithAssembly,
            production_assembly_source="fixed_layout_pose",
        )

        self.assertTrue(result.solved)
        self.assertEqual(result.constraint_count, 6)
        self.assertEqual(len(result.assembly.constraints), 6)
        self.assertEqual(result.metadata["production_assembly_source"], "fixed_layout_pose")
        self.assertEqual(result.metadata["placement_mode"], "constraint_solve_fixed_layout_pose")
        self.assertEqual(result.metadata["solver_constraint_mode"], "fixed_layout_pose_constraints")
        self.assertEqual(
            result.metadata["semantic_constraints_applied_to_solver"],
            "local_subassembly",
        )

    def test_can_disable_local_subassembly_solve(self) -> None:
        result = build_cadquery_assembly(
            self._layout(),
            cq_module=FakeCadQueryWithAssembly,
            local_subassembly_solve=False,
            production_assembly_source="fixed_layout_pose",
        )

        self.assertEqual(result.local_subassembly_results, [])
        self.assertEqual(result.metadata["semantic_constraint_application"], "none")
        self.assertEqual(result.metadata["semantic_constraints_applied_to_solver"], "none")
        self.assertFalse(result.metadata["local_subassembly_solve_requested"])
        self.assertEqual(result.metadata["local_subassembly_count"], 0)

    def test_part_placements_keep_dh_frame_z_offsets(self) -> None:
        layout = build_tabletop_serial_mechanical_layout(
            [
                DHParam(joint_id="J1", a=0, alpha=0, d=100, theta=0),
                DHParam(joint_id="J2", a=80, alpha=0, d=0, theta=0),
            ]
        )

        result = build_cadquery_assembly(
            layout,
            cq_module=FakeCadQueryWithAssembly,
        )

        self.assertEqual(result.part_locations["J1"]["loc"], (0.0, 0.0, 35.0))
        self.assertEqual(result.part_locations["L1"]["loc"], (0.0, 0.0, 85.0))
        self.assertEqual(result.part_locations["J2"]["loc"], (0.0, 0.0, 135.0))
        self.assertEqual(result.part_locations["J1"]["xDir"], (0.0, 0.0, 1.0))
        self.assertEqual(result.part_locations["L1"]["xDir"], (0.0, 0.0, 1.0))
        self.assertEqual(result.part_locations["L2"]["xDir"], (1.0, 0.0, 0.0))

    def test_can_set_solve_verbosity(self) -> None:
        result = build_cadquery_assembly(
            self._layout(),
            cq_module=FakeCadQueryWithAssembly,
            solve_verbosity=2,
        )

        self.assertTrue(result.solved)
        self.assertEqual(result.assembly.solve_calls, [2])
        self.assertEqual(len(result.assembly.constraints), 11)
        self.assertEqual(result.constraint_count, 11)

    def test_can_use_full_semantic_solve_as_production_assembly(self) -> None:
        result = build_cadquery_assembly(
            self._layout(),
            cq_module=FakeCadQueryWithAssembly,
        )

        self.assertTrue(result.solved)
        self.assertEqual(result.metadata["production_assembly_source"], "full_semantic_solve")
        self.assertTrue(result.metadata["production_full_semantic_solve_used"])
        self.assertEqual(result.metadata["semantic_constraints_applied_to_solver"], "full_assembly")
        self.assertEqual(result.metadata["placement_mode"], "full_semantic_solve")
        self.assertEqual(result.metadata["solver_constraint_mode"], "semantic_mate_constraints")
        self.assertEqual(result.constraint_count, 11)
        self.assertEqual(len(result.assembly.constraints), 11)
        self.assertEqual(result.assembly.constraints[0], ("base", "Fixed"))

        with tempfile.TemporaryDirectory() as temp_dir:
            package = export_robot_step_package(
                result,
                temp_dir,
                whole_machine_filename="whole.step",
                subassemblies=[],
            )

            self.assertEqual(package.whole_machine_assembly_source, "full_semantic_solve")
            self.assertTrue(package.whole_machine_export.exists)

    def test_full_semantic_solve_request_fails_when_fixture_gate_is_blocked(self) -> None:
        layout = self._layout()
        broken_layout = replace(
            layout,
            assembly_constraints=[
                constraint
                for constraint in layout.assembly_constraints
                if "end_effector" not in constraint.id
            ],
        )

        result = build_cadquery_assembly(
            broken_layout,
            cq_module=FakeCadQueryWithAssembly,
            production_assembly_source="full_semantic_solve",
            raise_on_full_semantic_solve_error=False,
        )

        self.assertFalse(result.solved)
        self.assertEqual(
            result.metadata["production_assembly_source"],
            "full_semantic_solve_failed",
        )
        self.assertFalse(result.metadata["production_full_semantic_solve_used"])
        self.assertIn("blocked", result.metadata["production_full_semantic_solve_error"])
        self.assertEqual(result.metadata["semantic_constraints_applied_to_solver"], "none")

    def test_can_capture_solve_error_without_raising(self) -> None:
        result = build_cadquery_assembly(
            self._layout(),
            cq_module=FailingCadQueryWithAssembly,
            raise_on_solve_error=False,
            production_assembly_source="fixed_layout_pose",
        )

        self.assertFalse(result.solved)
        self.assertEqual(result.solve_error, "solve failed")
        self.assertEqual(result.assembly.solve_calls, [0])

    def test_raises_solve_error_by_default(self) -> None:
        with self.assertRaises(RuntimeError):
            build_cadquery_assembly(
                self._layout(),
                cq_module=FailingCadQueryWithAssembly,
            )

    def test_rejects_disabling_constraint_solve(self) -> None:
        with self.assertRaisesRegex(ValueError, "constraint_solve"):
            build_cadquery_assembly(
                self._layout(),
                cq_module=FakeCadQueryWithAssembly,
                solve=False,
            )

    def test_exports_step(self) -> None:
        result = build_cadquery_assembly(
            self._layout(),
            cq_module=FakeCadQueryWithAssembly,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "robot.step"
            export_result = export_step(result.assembly, output_path)

            self.assertTrue(export_result.exists)
            self.assertEqual(export_result.export_type, "STEP")
            self.assertEqual(output_path.read_text(encoding="utf-8"), "STEP")
            self.assertEqual(result.assembly.saved[0]["exportType"], "STEP")

    def test_exports_robot_step_package(self) -> None:
        result = build_cadquery_assembly(
            self._layout(),
            cq_module=FakeCadQueryWithAssembly,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            package = export_robot_step_package(
                result,
                temp_dir,
                whole_machine_filename="whole.step",
                subassemblies=[
                    CadQuerySubassemblyExportSpec(
                        name="base",
                        part_ids=["base"],
                        part_export_names={"base": "base_body"},
                    ),
                    CadQuerySubassemblyExportSpec(
                        name="joint1",
                        part_ids=["J1"],
                        part_export_names={"J1": "joint1_housing"},
                    ),
                    CadQuerySubassemblyExportSpec(
                        name="link1",
                        part_ids=["L1"],
                        part_export_names={"L1": "link1_body"},
                    ),
                    CadQuerySubassemblyExportSpec(
                        name="joint2",
                        part_ids=["J2"],
                        part_export_names={"J2": "joint2_housing"},
                    ),
                    CadQuerySubassemblyExportSpec(
                        name="link2",
                        part_ids=["L2"],
                        part_export_names={"L2": "link2_body"},
                    ),
                    CadQuerySubassemblyExportSpec(
                        name="end_effector",
                        part_ids=["end_effector"],
                        part_export_names={"end_effector": "end_effector_mount"},
                    ),
                ],
            )

            self.assertEqual(package.root_dir, Path(temp_dir))
            self.assertEqual(package.whole_machine_export.path, Path(temp_dir) / "whole.step")
            self.assertTrue(package.whole_machine_export.exists)
            self.assertIsNotNone(package.whole_machine_export.bbox)
            self.assertTrue(package.whole_machine_export.bbox.valid)
            self.assertEqual(
                [item.name for item in package.subassemblies],
                ["base", "joint1", "link1", "joint2", "link2", "end_effector"],
            )
            self.assertEqual(package.subassemblies[0].part_ids, ["base"])
            self.assertEqual(package.subassemblies[1].part_ids, ["J1"])
            self.assertTrue(all(item.assembly_export.bbox for item in package.subassemblies))
            self.assertEqual(package.file_count, 13)

            self.assertTrue((Path(temp_dir) / "base" / "base_body.step").exists())
            self.assertTrue((Path(temp_dir) / "base" / "base.step").exists())
            self.assertTrue((Path(temp_dir) / "joint1" / "joint1_housing.step").exists())
            self.assertTrue((Path(temp_dir) / "joint1" / "joint1.step").exists())
            self.assertTrue((Path(temp_dir) / "link1" / "link1_body.step").exists())
            self.assertTrue((Path(temp_dir) / "link1" / "link1.step").exists())
            self.assertTrue((Path(temp_dir) / "end_effector" / "end_effector_mount.step").exists())
            self.assertTrue((Path(temp_dir) / "end_effector" / "end_effector.step").exists())

    def test_exports_local_solved_subassembly_when_spec_references_it(self) -> None:
        result = build_cadquery_assembly(
            self._layout(),
            cq_module=FakeCadQueryWithAssembly,
        )
        local_result = result.local_subassembly_results[0]
        local_part_count = len(local_result.assembly.parts)

        with tempfile.TemporaryDirectory() as temp_dir:
            package = export_robot_step_package(
                result,
                temp_dir,
                whole_machine_filename="whole.step",
                subassemblies=[
                    CadQuerySubassemblyExportSpec(
                        name="joint2_link2_end_effector",
                        part_ids=["J2", "L2", "end_effector"],
                        part_export_names={
                            "J2": "joint2_housing",
                            "L2": "link2_body",
                            "end_effector": "end_effector_mount",
                        },
                        source_local_subassembly_name=local_result.name,
                    ),
                ],
            )

            exported = package.subassemblies[0]
            self.assertEqual(exported.assembly_source, "local_constraint_solve")
            self.assertEqual(exported.local_solve_usage, "experimental_local_solve")
            self.assertEqual(exported.source_local_subassembly_name, local_result.name)
            self.assertIsNotNone(exported.assembly_export.bbox)
            self.assertTrue(exported.assembly_export.bbox.valid)
            self.assertEqual(len(local_result.assembly.parts), local_part_count)
            self.assertEqual(
                local_result.assembly.saved[0]["path"],
                str(Path(temp_dir) / "joint2_link2_end_effector" / "joint2_link2_end_effector.step"),
            )
            self.assertTrue(exported.assembly_export.exists)

    def test_export_requires_save_method(self) -> None:
        with self.assertRaises(TypeError):
            export_step(object(), "missing.step")


if __name__ == "__main__":
    unittest.main()
