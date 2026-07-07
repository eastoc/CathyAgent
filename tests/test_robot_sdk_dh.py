import unittest

import numpy as np
import numpy.testing as npt

from robot_sdk.kinematics.dh import (
    dh_transform,
    estimate_reach,
    forward_kinematics,
    forward_kinematics_chain,
    matrix_multiply,
    position_from_matrix,
)
from robot_sdk.types import DHParam


class RobotSdkDhTest(unittest.TestCase):
    def test_standard_dh_transform_for_zero_angles(self) -> None:
        transform = dh_transform(
            DHParam(joint_id="J1", a=100, alpha=0, d=20, theta=0)
        )

        self.assertIsInstance(transform, np.ndarray)
        self.assertMatrixAlmostEqual(
            transform,
            np.array(
                [
                    [1.0, 0.0, 0.0, 100.0],
                    [0.0, 1.0, -0.0, 0.0],
                    [0.0, 0.0, 1.0, 20.0],
                    [0.0, 0.0, 0.0, 1.0],
                ]
            ),
        )

    def test_standard_dh_transform_converts_degrees(self) -> None:
        transform = dh_transform(
            DHParam(
                joint_id="J1",
                a=100,
                alpha=0,
                d=0,
                theta=90,
                angle_unit="deg",
            )
        )

        self.assertTupleAlmostEqual(position_from_matrix(transform), (0.0, 100.0, 0.0))

    def test_forward_kinematics_for_two_planar_links(self) -> None:
        params = [
            DHParam(joint_id="J1", a=100, alpha=0, d=0, theta=0),
            DHParam(joint_id="J2", a=50, alpha=0, d=0, theta=0),
        ]

        transform = forward_kinematics(params)

        self.assertTupleAlmostEqual(position_from_matrix(transform), (150.0, 0.0, 0.0))

    def test_forward_kinematics_accepts_joint_values_by_id(self) -> None:
        params = [
            DHParam(joint_id="J1", a=100, alpha=0, d=0, theta=0, angle_unit="deg"),
            DHParam(joint_id="J2", a=50, alpha=0, d=0, theta=0, angle_unit="deg"),
        ]

        transform = forward_kinematics(params, joint_values={"J1": 90, "J2": 0})

        self.assertTupleAlmostEqual(position_from_matrix(transform), (0.0, 150.0, 0.0))

    def test_forward_kinematics_accepts_prismatic_joint_value(self) -> None:
        params = [
            DHParam(
                joint_id="J1",
                a=0,
                alpha=0,
                d=10,
                theta=0,
                joint_type="prismatic",
            )
        ]

        transform = forward_kinematics(params, joint_values={"J1": 42})

        self.assertTupleAlmostEqual(position_from_matrix(transform), (0.0, 0.0, 42.0))

    def test_forward_kinematics_chain_includes_base_by_default(self) -> None:
        params = [
            DHParam(joint_id="J1", a=100, alpha=0, d=0, theta=0),
            DHParam(joint_id="J2", a=50, alpha=0, d=0, theta=0),
        ]

        transforms = forward_kinematics_chain(params)

        self.assertEqual(len(transforms), 3)
        self.assertTupleAlmostEqual(position_from_matrix(transforms[0]), (0.0, 0.0, 0.0))
        self.assertTupleAlmostEqual(position_from_matrix(transforms[-1]), (150.0, 0.0, 0.0))

    def test_estimate_reach_sums_geometric_link_offsets(self) -> None:
        params = [
            DHParam(joint_id="J1", a=300, alpha=0, d=0, theta=0),
            DHParam(joint_id="J2", a=0, alpha=0, d=400, theta=0),
        ]

        self.assertAlmostEqual(estimate_reach(params), 700.0)

    def test_matrix_multiply_composes_translations(self) -> None:
        first = DHParam(joint_id="J1", a=100, alpha=0, d=0, theta=0)
        second = DHParam(joint_id="J2", a=50, alpha=0, d=0, theta=0)

        transform = matrix_multiply(dh_transform(first), dh_transform(second))

        self.assertTupleAlmostEqual(position_from_matrix(transform), (150.0, 0.0, 0.0))

    def test_poe_convention_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            dh_transform(
                DHParam(joint_id="J1", a=0, alpha=0, d=0, theta=0),
                convention="poe",
            )

    def assertTupleAlmostEqual(
        self,
        actual: tuple[float, float, float],
        expected: tuple[float, float, float],
        *,
        places: int = 7,
    ) -> None:
        self.assertEqual(len(actual), len(expected))
        for actual_value, expected_value in zip(actual, expected, strict=True):
            self.assertAlmostEqual(actual_value, expected_value, places=places)

    def assertMatrixAlmostEqual(
        self,
        actual: np.ndarray,
        expected: np.ndarray,
        *,
        places: int = 7,
    ) -> None:
        npt.assert_allclose(actual, expected, atol=10**-places)


if __name__ == "__main__":
    unittest.main()
