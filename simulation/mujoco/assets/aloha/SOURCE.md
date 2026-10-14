# ALOHA 资产来源

来源：[huggingface/gym-aloha](https://github.com/huggingface/gym-aloha/tree/bd3325740ea8d1c97411c41ea1e0f4ce0a7de8da/gym_aloha/assets)。
固定提交：`bd3325740ea8d1c97411c41ea1e0f4ce0a7de8da`。
资产和上游许可证原样保留；下载器逐文件验证 Git blob SHA-1。
这是 ALOHA / ViperX 300s，不是 Panda 或 ALOHA 2。

`simulation/mujoco/vla/aloha_scene.py` 在内存组装自定义场景：保留双臂、
关节、位置执行器和原相机位置；腕部视野从20度拓宽为可配置的75度；
替换任务物体与桌面，移除旧 keyframe，
固定 0.002 秒物理步长。不会改动这些上游文件。

这不是官方 ALOHA TransferCube benchmark；π0.5 基础模型在本自定义场景的
任务成功率未经验证。
