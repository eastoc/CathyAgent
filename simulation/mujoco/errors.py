"""MuJoCo 扩展的稳定错误类型。"""


class MujocoSimulationError(RuntimeError):
    error_code = "mujoco_error"


class MujocoDependencyError(MujocoSimulationError):
    error_code = "mujoco_dependency_missing"


class MujocoConfigError(MujocoSimulationError):
    error_code = "mujoco_config_error"


class MujocoExecutionError(MujocoSimulationError):
    error_code = "mujoco_execution_error"


class MujocoIKError(MujocoExecutionError):
    error_code = "ik_failed"


class MujocoCancelledError(MujocoExecutionError):
    error_code = "motion_cancelled"
