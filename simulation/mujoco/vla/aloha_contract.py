"""本地与 AutoDL 共用的 ALOHA 数据契约；可与服务脚本一起单独复制。"""
import numpy as np

CONTRACT_VERSION = "aloha_joint_position_v1"
ACTION_REPRESENTATION = "absolute_joint_position"
CAMERAS = ("cam_high", "cam_left_wrist", "cam_right_wrist")
ARM_INDICES = np.array([0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12])
GRIPPER_INDICES = np.array([6, 13])
# 与 gym-aloha/constants.py 和 OpenPI AlohaInputs 的标准 ALOHA 空间一致。
GRIPPER_CLOSE = 0.01844
GRIPPER_OPEN = 0.05800


def validate_state(state):
    value = np.asarray(state, dtype=np.float32)
    if value.shape != (14,) or not np.isfinite(value).all():
        raise ValueError("ALOHA state 必须是14维有限数：左臂6+夹爪1+右臂6+夹爪1")
    if np.abs(value[ARM_INDICES]).max() > 2 * np.pi:
        raise ValueError("ALOHA 关节状态单位必须是弧度")
    if ((value[GRIPPER_INDICES] < -0.1) | (value[GRIPPER_INDICES] > 1.1)).any():
        raise ValueError("ALOHA 夹爪状态必须按标准 ALOHA 范围归一化到约0..1")
    return value


def validate_actions(actions):
    value = np.asarray(actions, dtype=np.float64)
    if value.ndim != 2 or value.shape[1] != 14 or not 1 <= value.shape[0] <= 512:
        raise ValueError("ALOHA actions 必须是[1..512,14]，不能使用 LIBERO 7维动作")
    if not np.isfinite(value).all():
        raise ValueError("ALOHA actions 包含 NaN/Inf")
    if np.abs(value[:, ARM_INDICES]).max() > 2 * np.pi:
        raise ValueError("ALOHA 关节目标明显超界；检查是否错误归一化/使用角度制")
    if ((value[:, GRIPPER_INDICES] < -0.25) | (value[:, GRIPPER_INDICES] > 1.25)).any():
        raise ValueError("ALOHA 夹爪目标明显超界；检查 checkpoint 与 transforms")
    # 不是 OSC 增量，不能整体裁剪到 [-1,1]；物理关节限位由 runtime 实施。
    return value.copy()


def pack_state(robot_qpos):
    q = np.asarray(robot_qpos)
    if q.shape != (16,):
        raise ValueError("ALOHA 物理机器人状态应为16维（每臂6关节+2手指）")
    state = np.concatenate((q[:6], [(q[6] - GRIPPER_CLOSE) / (GRIPPER_OPEN - GRIPPER_CLOSE)],
                            q[8:14], [(q[14] - GRIPPER_CLOSE) / (GRIPPER_OPEN - GRIPPER_CLOSE)]))
    return validate_state(state)


def unpack_action(action):
    a = validate_actions([action])[0]
    fingers = GRIPPER_CLOSE + np.clip(a[GRIPPER_INDICES], 0, 1) * (GRIPPER_OPEN - GRIPPER_CLOSE)
    return np.concatenate((a[:6], [fingers[0], -fingers[0]], a[7:13], [fingers[1], -fingers[1]]))
