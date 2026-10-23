# AutoDL：π0.5 ALOHA 部署与 MCP

使用官方 **pi05_aloha 配置 + pi05_base 权重**，不是将 pi05_libero 改成14维。
`serve_policy.py --env ALOHA_SIM` 默认会加载 π0 的仿真专用权重，不是本方案。
官方对应关系见 [serve_policy.py](https://github.com/Physical-Intelligence/openpi/blob/main/scripts/serve_policy.py)。
本方案是自定义 ALOHA 仿真实验；基础权重不保证开箱即用的抓放成功率。

## 1. 上传三个文件

在 AutoDL 已有 OpenPI 仓库下建立 `cathy_aloha` 文件夹，将本目录的三个文件上传进去：

```text
/root/autodl-tmp/vla/openpi/cathy_aloha/
  aloha_contract.py
  serve_aloha_policy.py
  aloha_mcp_server.py
```

后续命令均在 `/root/autodl-tmp/vla/openpi` 执行。沿用已能运行 π 的 OpenPI 环境，
不要为本地仿真在 AutoDL 安装 robosuite、MuJoCo 或 gym-aloha。
不需要重新 `uv sync`；下面使用 `--no-sync`，避免更改已经可用的环境。
如果当前的 OpenPI 找不到 `pi05_aloha`，先核对仓库版本，勿用 LIBERO 配置冒充。

## 2. 终端一：启动新模型服务

先在旧模型所在终端 Ctrl+C 停止旧服务，释放8000端口和显存。不要同时加载两套大权重。
也在旧 MCP 所在终端 Ctrl+C 停止旧代理，释放6006端口；不需要删除旧权重或代码。

```bash
cd /root/autodl-tmp/vla/openpi
XLA_PYTHON_CLIENT_MEM_FRACTION=0.8 uv run --no-sync python cathy_aloha/serve_aloha_policy.py \
  --port 8000 \
  --checkpoint gs://openpi-assets/checkpoints/pi05_base
```

首次运行会下载新权重并加载 GPU；网络不通时可使用已下载 checkpoint 的本地目录替代 URI。
本地目录应含正确权重和 `assets/trossen` 归一化统计，不能用 LIBERO 统计替代。
本脚本复用官方 `create_trained_policy`，额外在 WebSocket 握手中标明契约和配置。
模型仅监听 `127.0.0.1:8000`，不要将此端口作为 MCP 暴露。

官方原生的等价模型加载命令是：

```bash
uv run --no-sync scripts/serve_policy.py --port 8000 policy:checkpoint \
  --policy.config pi05_aloha --policy.dir gs://openpi-assets/checkpoints/pi05_base
```

但本套 MCP 为防止误连旧模型，要求额外握手字段，**配套运行必须使用上面的
cathy_aloha/serve_aloha_policy.py**。两种模型服务不要同时启动。

## 3. 终端二：启动轻量 MCP 代理

建议独立的轻量环境，以免 FastMCP 依赖影响 OpenPI 的锁定依赖：

```bash
cd /root/autodl-tmp/vla/openpi
uv venv --python 3.11 .venv-aloha-mcp
uv pip install --python .venv-aloha-mcp/bin/python \
  -e packages/openpi-client "fastmcp==3.2.4" "pillow>=10" "numpy>=1.26,<3"

.venv-aloha-mcp/bin/python cathy_aloha/aloha_mcp_server.py --port 6006
```

模型服务应先启动完毕，否则 MCP 会等待 WebSocket 可用。首个推理可能发生 JIT 编译。
默认 MCP 监听 `127.0.0.1:6006/mcp`，适用于同容器反向代理。
只有代理需要从容器外访问时，才显式加 `--host 0.0.0.0`。
公网推理服务应加访问控制／鉴权，不能将保密域名当作访问控制。
此示例未内置用户身份管理；不要将无鉴权端口长期直接公开。

云端工具：

- `pi_aloha_info`：检查契约，不调用 GPU。
- `pi_aloha_smoke_test`：黑图／假状态推理，不执行机器人，不证明任务成功。
- `pi_aloha_predict`：真实观测推理，返回 `actions[H,14]`。

同一 WebSocket 上仅允许一个推理请求，避免响应错配；忙时拒绝额外请求。
连接异常后服务拒绝继续推理，需要重启 MCP 重新连接。重启模型后也应重启 MCP。

## 4. 终端三：检查 MCP

在 OpenPI 目录执行，先看 info；首次确认模型就绪后再取消 smoke 行的注释：

```bash
.venv-aloha-mcp/bin/python - <<'PY'
import asyncio
from fastmcp import Client

async def main():
    async with Client("http://127.0.0.1:6006/mcp", timeout=180) as client:
        print([tool.name for tool in await client.list_tools()])
        print(await client.call_tool("pi_aloha_info", {}))
        # print(await client.call_tool("pi_aloha_smoke_test", {}))

asyncio.run(main())
PY
```

info 应包含 `policy_config=pi05_aloha`、`state_dim=14`、`action_dim=14`。
若仍看到 `pi_predict`，说明公网或本地端口还指向旧 MCP 服务。

## 5. 接回 CathyAgent

保持已有 AutoDL 8443 入口映射到 MCP 的6006端口，CathyAgent 配置仍为：

```yaml
MCP:
  enabled: true
  mcp_servers:
    robot_vla:
      url: "https://u548917-0xtl-dfab8bd0.westb.seetacloud.com:8443/mcp"
```

此后端不再通过 `simulation.mujoco` 的 `control_mode` 启动；保留部署文档以支持
底层策略服务和回归测试。宿主不会将旧 LIBERO 的7维动作交给 ALOHA。
本地 ALOHA 场景已真实测试；本文件的 AutoDL 新权重部署尚未替你执行。

参考：[官方 ALOHA 配置](https://github.com/Physical-Intelligence/openpi/blob/main/src/openpi/training/config.py)、
[ALOHA 输入输出与夹爪转换](https://github.com/Physical-Intelligence/openpi/blob/main/src/openpi/policies/aloha_policy.py)、
[官方远程客户端](https://github.com/Physical-Intelligence/openpi/blob/main/packages/openpi-client/src/openpi_client/websocket_client_policy.py)。
