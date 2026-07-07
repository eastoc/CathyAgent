import math
import unittest

from robot_sdk.kinematics import (
    KinematicScalingRequest,
    build_profile_kinematic_model,
)
from robot_sdk.kinematics.dh import estimate_reach
from robot_sdk.kinematics.profiles import require_profile


class RobotSdkKinematicScalingTest(unittest.TestCase):
    def test_exact_profile_keeps_ur3e_dh_values(self) -> None:
        result = build_profile_kinematic_model(
            KinematicScalingRequest(profile_name="ur3e")
        )

        self.assertEqual(result.mode, "exact_profile")
        self.assertEqual(result.scale_factor, 1.0)
        self.assertAlmostEqual(result.source_reach, 917.1)
        self.assertAlmostEqual(result.model.dh_params[1].a, -243.55)
        self.assertAlmostEqual(result.model.dh_params[2].a, -213.2)
        self.assertAlmostEqual(result.model.dh_params[0].alpha, math.pi / 2)

    def test_scaled_profile_scales_only_dh_length_terms(self) -> None:
        source = require_profile("ur3e")
        result = build_profile_kinematic_model(
            KinematicScalingRequest(
                profile_name="UR3 e",
                mode="scaled_profile",
                target_reach=1834.2,
            )
        )

        self.assertEqual(result.mode, "scaled_profile")
        self.assertAlmostEqual(result.scale_factor, 2.0)
        self.assertAlmostEqual(result.model.dh_params[0].d, source.dh_params[0].d * 2)
        self.assertAlmostEqual(result.model.dh_params[1].a, source.dh_params[1].a * 2)
        self.assertAlmostEqual(result.model.dh_params[2].a, source.dh_params[2].a * 2)
        self.assertAlmostEqual(result.model.dh_params[0].alpha, source.dh_params[0].alpha)
        self.assertAlmostEqual(result.model.dh_params[4].alpha, source.dh_params[4].alpha)
        self.assertAlmostEqual(result.model.dh_params[3].theta, source.dh_params[3].theta)
        self.assertTrue(any("no longer official" in item for item in result.warnings))

    def test_scaled_profile_accepts_target_reach_in_meters(self) -> None:
        result = build_profile_kinematic_model(
            KinematicScalingRequest(
                profile_name="ur3e",
                mode="scaled_profile",
                target_reach=1.8342,
                target_reach_unit="m",
            )
        )

        self.assertAlmostEqual(result.target_reach, 1834.2)
        self.assertAlmostEqual(result.scale_factor, 2.0)

    def test_scaled_profile_requires_target_reach(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires target_reach"):
            build_profile_kinematic_model(
                KinematicScalingRequest(profile_name="ur3e", mode="scaled_profile")
            )

    def test_request_rejects_non_positive_target_reach(self) -> None:
        with self.assertRaisesRegex(ValueError, "target_reach must be positive"):
            KinematicScalingRequest(
                profile_name="ur3e",
                mode="scaled_profile",
                target_reach=0,
            )

    def test_profile_like_template_uses_target_reach_without_claiming_official_dh(
        self,
    ) -> None:
        result = build_profile_kinematic_model(
            KinematicScalingRequest(
                profile_name="ur3e",
                mode="profile_like_template",
                target_reach=1200,
            )
        )

        self.assertEqual(result.mode, "profile_like_template")
        self.assertAlmostEqual(estimate_reach(result.model.dh_params), 1200.0)
        self.assertAlmostEqual(result.model.dh_params[0].alpha, math.pi / 2)
        self.assertAlmostEqual(result.model.dh_params[4].alpha, -math.pi / 2)
        self.assertNotAlmostEqual(result.model.dh_params[1].a, -243.55)
        self.assertTrue(any("not official" in item for item in result.warnings))
        self.assertTrue(any("profile-like template" in item for item in result.assumptions))

    def test_profile_like_template_requires_target_reach(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires target_reach"):
            build_profile_kinematic_model(
                KinematicScalingRequest(
                    profile_name="ur3e",
                    mode="profile_like_template",
                )
            )


if __name__ == "__main__":
    unittest.main()
