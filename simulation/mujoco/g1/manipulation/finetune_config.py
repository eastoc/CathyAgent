"""在 Isaac-GR00T 中通过 --modality-config-path 导入；仅用于自定义夹爪数据。"""
from gr00t.configs.data.embodiment_configs import register_modality_config
from gr00t.data.embodiment_tags import EmbodimentTag
from gr00t.data.types import (
    ActionConfig, ActionFormat, ActionRepresentation, ActionType, ModalityConfig,
)

KEYS = ["left_wrist_eef_9d", "right_wrist_eef_9d", "left_hand", "right_hand",
        "left_arm", "right_arm", "waist"]
configs = []
for key in KEYS:
    eef = key.endswith("eef_9d")
    relative = eef or key.endswith("arm")
    configs.append(ActionConfig(
        rep=ActionRepresentation.RELATIVE if relative else ActionRepresentation.ABSOLUTE,
        type=ActionType.EEF if eef else ActionType.NON_EEF,
        format=ActionFormat.XYZ_ROT6D if eef else ActionFormat.DEFAULT,
        state_key=key,
    ))

register_modality_config({
    "video": ModalityConfig(delta_indices=[-20, 0], modality_keys=["ego_view"]),
    "state": ModalityConfig(delta_indices=[0], modality_keys=KEYS),
    "action": ModalityConfig(delta_indices=list(range(40)), modality_keys=KEYS, action_configs=configs),
    "language": ModalityConfig(delta_indices=[0], modality_keys=["annotation.human.task_description"]),
}, embodiment_tag=EmbodimentTag.NEW_EMBODIMENT)
