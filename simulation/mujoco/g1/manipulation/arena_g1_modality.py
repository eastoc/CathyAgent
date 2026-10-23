"""Arena 官方 G1 WBC 的 GR00T N1.7 modality；服务端通过路径导入。"""

from gr00t.configs.data.embodiment_configs import register_modality_config
from gr00t.data.embodiment_tags import EmbodimentTag
from gr00t.data.types import (
    ActionConfig, ActionFormat, ActionRepresentation, ActionType, ModalityConfig,
)


STATE_KEYS = ["left_arm", "right_arm", "left_hand", "right_hand", "waist"]
ACTION_KEYS = [*STATE_KEYS, "base_height_command", "navigate_command"]

unitree_g1_sim_wbc_config = {
    "video": ModalityConfig(delta_indices=[0], modality_keys=["ego_view"]),
    "state": ModalityConfig(delta_indices=[0], modality_keys=STATE_KEYS),
    "action": ModalityConfig(
        delta_indices=list(range(40)),
        modality_keys=ACTION_KEYS,
        action_configs=[
            ActionConfig(
                rep=ActionRepresentation.ABSOLUTE,
                type=ActionType.NON_EEF,
                format=ActionFormat.DEFAULT,
            )
            for _ in ACTION_KEYS
        ],
    ),
    "language": ModalityConfig(
        delta_indices=[0], modality_keys=["annotation.human.task_description"]
    ),
}

register_modality_config(
    unitree_g1_sim_wbc_config,
    embodiment_tag=EmbodimentTag.NEW_EMBODIMENT,
)
