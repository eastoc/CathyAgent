"""在 AutoDL 的 OpenPI 环境运行；只加载一次 π0.5 权重。"""
import argparse
import logging

if __package__:
    from .aloha_contract import ACTION_REPRESENTATION, CAMERAS, CONTRACT_VERSION
else:
    from aloha_contract import ACTION_REPRESENTATION, CAMERAS, CONTRACT_VERSION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", default="gs://openpi-assets/checkpoints/pi05_base")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    # 延迟导入，使 --help 不加载 GPU 库。
    from openpi.policies import policy_config
    from openpi.training import config
    from openpi.serving.websocket_policy_server import WebsocketPolicyServer

    cfg = config.get_config("pi05_aloha")
    policy = policy_config.create_trained_policy(cfg, args.checkpoint)
    metadata = dict(cfg.policy_metadata or {})
    metadata.update(contract_version=CONTRACT_VERSION, action_representation=ACTION_REPRESENTATION,
                    policy_config="pi05_aloha", state_dim=14, action_dim=14,
                    cameras=list(CAMERAS), control_hz=50)
    # π0.5 内部32维、delta/符号/夹爪/统计转换均由官方 policy transforms 处理。
    # 对外已经是标准 ALOHA 14维绝对关节目标，不要再次反归一化或累加当前状态。
    WebsocketPolicyServer(policy, host="127.0.0.1", port=args.port, metadata=metadata).serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
