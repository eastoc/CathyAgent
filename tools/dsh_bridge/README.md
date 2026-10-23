# Codex → DeepSeek Harness 桥

把 DSH 包成一个 MCP stdio server，让 Codex（或 Claude Code 等任何 MCP 客户端）通过 `dsh_run` 工具把任务发给 DSH 执行，并拿回最终答复。

## 1. 让 `dsh` 出现在 PATH

DSH 仓库已构建好 CLI（`apps/cli/lib/bin.js`）。放到 PATH：

```sh
mkdir -p ~/.local/bin
cat > ~/.local/bin/dsh <<'SH'
#!/bin/sh
exec node /Users/east_wu/EastProject/deepseek-harness/apps/cli/lib/bin.js "$@"
SH
chmod +x ~/.local/bin/dsh
dsh --version   # 0.2.1-rc.2
```

## 2. 先手动跑通一次 headless（首次会自动创建 `~/.dsh/profiles/headless`）

```sh
dsh --profile headless "用一句话说明这个仓库是做什么的"
```

headless 默认**不读** GUI 保存的登录凭据，只按 `环境变量 → 当前目录/.env → $DSH_HOME/.env` 找 key。
想复用 GUI 已保存的凭据，在 `~/.dsh/profiles/headless/cordis.patch.yml` 追加：

```yaml
- id: credentials
  disabled: false
- id: authorization
  disabled: false
- id: deepseek-account
  disabled: false
```

## 3. 注册到 Codex

```sh
codex mcp add dsh -- node /Users/east_wu/EastProject/CathyAgent/tools/dsh_bridge/dsh-mcp.mjs
codex mcp list
```

在 `~/.codex/config.toml` 中会生成：

```toml
[mcp_servers.dsh]
command = "node"
args = ["/Users/east_wu/EastProject/CathyAgent/tools/dsh_bridge/dsh-mcp.mjs"]
```

需要指定凭据/二进制时加环境变量：

```sh
codex mcp add dsh --env DEEPSEEK_API_KEY=sk-xxx -- node .../dsh-mcp.mjs
```

## 4. 使用

重启 Codex 会话后，直接说：「用 dsh_run 让 DSH 在 /path/to/repo 里补测试」。
工具入参：

| 参数 | 必填 | 说明 |
|---|---|---|
| `task` | 是 | 任务原文，走 dsh 的 stdin |
| `cwd` | 否 | dsh 的工作目录，默认桥进程 cwd |
| `session_id` | 否 | 上次答复尾部 `[dsh session_id: …]` 里的 id，用于继续同一段 DSH 会话 |

环境变量：`DSH_BIN`（可写 `"node /path/bin.js"`）、`DSH_BRIDGE_PROFILE`（默认 headless）、`DSH_TIMEOUT_MS`（默认 30 分钟）。

## 备选：不装 MCP，直接 shell 调

Codex 的 shell 工具里执行同样的命令即可（一次性任务，最省事）：

```sh
dsh --profile headless --json "任务"        # 事件流：session / text / thinking / tool_call / tool_result / final
dsh --profile headless --session-id <id> "继续"  # 续会话
```

注意：Codex 的 shell 沙箱默认禁网，模型请求可能失败；MCP server 由 Codex 主进程拉起，通常不受 shell 沙箱限制，所以长期协作用 MCP 桥更稳。

## 反向：DSH 也能调 Codex

在 DSH Web/Desktop 的插件页启用 **Codex Subagent**（`@deepseek-ai/dsh-subagent-codex`），DSH 就多了 `subagent_codex` 工具，可把子任务派给一个全新的、非交互的 Codex 线程。

两个方向同时开时要防递归：要么只开单向，要么在被驱动的一侧关掉对方的 subagent 工具 / 限制 `maxDepth`。

## 协作避坑

1. **同一份工作区**：两边都在同一个 repo 根目录跑，靠 `AGENTS.md` 统一回答语言、提交规范。
2. **串行改文件**：不要让两个 agent 同时写同一批文件；先 `git commit`，再交接，必要时用 `git worktree` 分离。
3. **角色分工**：Codex 负责拆解/评审/验收，DSH 负责批量执行，结果以文件（diff / 报告）为交接物，而不是靠对话记忆。
4. **会话即上下文**：DSH 用 `--session-id` 续聊，Codex 用同一 thread；跨 agent 只传结论和文件路径。
