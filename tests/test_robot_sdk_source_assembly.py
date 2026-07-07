import tempfile
import unittest
from pathlib import Path

import build123d as b

from robot_sdk.cad.bbox import bounding_box_from_cad_object
from robot_sdk.cad.source_assembly import SourceAssemblyHelper, export_source_step


class RobotSdkSourceAssemblyTest(unittest.TestCase):
    def test_connects_part_local_joints_with_build123d(self) -> None:
        helper = SourceAssemblyHelper("source_joint_demo")
        base = helper.add(b.Box(10, 10, 10), "base")
        link = helper.add(b.Box(5, 5, 5), "link")
        helper.rigid_frame(base, "top", b.Location((0, 0, 5)))
        helper.rigid_frame(link, "bottom", b.Location((0, 0, -2.5)))

        relation = helper.face_to_face((base, "top"), (link, "bottom"))
        assembly = helper.build()

        self.assertEqual(relation.relation, "face_to_face")
        self.assertEqual(len(helper.relations), 1)
        self.assertEqual(assembly.assembly_mates[0]["fixed"], "top")
        self.assertEqual(assembly.assembly_mates[0]["moving"], "bottom")
        self.assertAlmostEqual(float(link.center().Z), 7.5)

    def test_exports_resolved_static_step(self) -> None:
        helper = SourceAssemblyHelper("source_joint_export")
        base = helper.add(b.Box(10, 10, 10), "base")
        link = helper.add(b.Box(5, 5, 5), "link")
        helper.rigid_frame(base, "top", b.Location((0, 0, 5)))
        helper.rigid_frame(link, "bottom", b.Location((0, 0, -2.5)))
        helper.connect((base, "top"), (link, "bottom"))

        output_path = Path(tempfile.gettempdir()) / "cathy_source_joint_test.step"
        result = export_source_step(helper.build(), output_path)

        self.assertTrue(result.exists)
        self.assertGreater(result.size_bytes or 0, 0)
        self.assertIsNotNone(result.bbox)
        self.assertTrue(result.bbox.valid)
        self.assertGreater(result.bbox.zmax, 5.0)

    def test_reads_build123d_bounding_box(self) -> None:
        bbox = bounding_box_from_cad_object(b.Box(10, 20, 30))

        self.assertIsNotNone(bbox)
        self.assertEqual(bbox.xlen, 10.0)
        self.assertEqual(bbox.ylen, 20.0)
        self.assertEqual(bbox.zlen, 30.0)


if __name__ == "__main__":
    unittest.main()
