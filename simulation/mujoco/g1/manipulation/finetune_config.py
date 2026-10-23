"""兼容旧命令；实际 Arena GN1x modality 定义见 :mod:`arena_g1_modality`。"""

try:
    from .arena_g1_modality import *  # noqa: F401,F403
except ImportError:
    from arena_g1_modality import *  # type: ignore # noqa: F401,F403
