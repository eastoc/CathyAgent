import math
import unittest

from robot_sdk.kinematics.profiles import (
    RobotKinematicProfile,
    available_profiles,
    get_profile,
    profile_names,
    require_profile,
)
from robot_sdk.types import DHParam


class RobotSdkKinematicProfilesTest(unittest.TestCase):
    def test_available_profiles_includes_ur3e(self) -> None:
        self.assertIn("ur3e", profile_names())
        self.assertIn("ur3e", {profile.name for profile in available_profiles()})

    def test_ur3e_alias_lookup_is_readable_and_case_insensitive(self) -> None:
        self.assertIs(get_profile("UR3 e"), get_profile("ur3e"))
        self.assertIs(get_profile("ur-3e"), get_profile("ur3e"))
        self.assertIs(get_profile("Universal Robots UR3e"), get_profile("ur3e"))

    def test_free_form_requirement_is_not_auto_selected_by_sdk(self) -> None:
        self.assertIsNone(get_profile("参考 UR3E 做一个 6 轴机械臂"))

    def test_ur3e_profile_has_expected_dh_rows(self) -> None:
        profile = require_profile("ur3e")

        self.assertEqual(profile.dof, 6)
        self.assertEqual(profile.convention, "dh")
        self.assertEqual(profile.units, "mm")
        self.assertAlmostEqual(profile.dh_params[0].d, 151.85)
        self.assertAlmostEqual(profile.dh_params[0].alpha, math.pi / 2)
        self.assertAlmostEqual(profile.dh_params[1].a, -243.55)
        self.assertAlmostEqual(profile.dh_params[2].a, -213.2)
        self.assertAlmostEqual(profile.dh_params[5].d, 92.1)

    def test_profile_to_kinematic_model(self) -> None:
        model = require_profile("UR3e").to_kinematic_model()

        self.assertEqual(model.convention, "dh")
        self.assertEqual(len(model.joints), 6)
        self.assertEqual(len(model.links), 6)
        self.assertEqual(len(model.dh_params), 6)
        self.assertEqual(model.joints[0].parent_link, "base")
        self.assertEqual(model.joints[-1].child_link, "L6")
        self.assertTrue(any("Profile source:" in item for item in model.assumptions))
        self.assertTrue(model.warnings)

    def test_clone_dh_params_returns_independent_objects(self) -> None:
        profile = require_profile("ur3e")
        cloned = profile.clone_dh_params()

        cloned[0].d = 999

        self.assertAlmostEqual(profile.dh_params[0].d, 151.85)

    def test_require_profile_raises_for_unknown_profile(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown kinematic profile"):
            require_profile("not-a-robot")

    def test_profile_rejects_poe_convention(self) -> None:
        with self.assertRaises(ValueError):
            RobotKinematicProfile(
                name="bad",
                aliases=[],
                manufacturer=None,
                convention="poe",
                units="mm",
                dh_params=[DHParam(joint_id="J1", a=0, alpha=0, d=1, theta=0)],
                source="test",
            )


if __name__ == "__main__":
    unittest.main()
