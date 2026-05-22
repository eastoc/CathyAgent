# CathyAgent

借鉴 Claude Code 设计哲学的本地 Agent harness。详细架构见 `ARCHITECTURE.md`，分阶段路线图见 `ROADMAP.md`。

## 快速开始

### 1. 安装依赖

```bash
conda activate agent   # 或你自己的 Python 3.10+ 环境
pip install -r requirements.txt
```

### 2. 配置密钥

`config/.env` 已包含示例 key（仅本地开发，已被 `.gitignore` 忽略）。如需替换：

```env
OPENAI_API_KEY=sk-...
DEEPSEEK_API_KEY=sk-...
QWEN_API_KEY=sk-...
TAVILY_API_KEY=tvly-...
```

在 `config/config.yaml` 里切换 `LLM.provider`（`openai` / `deepseek` / `qwen`），各供应商连接信息见 `config/llm/<name>.yaml`。通用参数如 `temperature`、`max_tokens` 也在 `config/config.yaml` 的 `LLM` 段配置。OpenAI 的 GPT-5 / o-series 模型会自动适配 `max_completion_tokens` 和默认 temperature，无需手动改请求参数。

系统提示词在 `cathy/context.py`；项目级规则可写入项目根的 `AGENTS.md` 或 `CATHY.md`，会自动注入。

### 3. 运行

```bash
python main.py
# 或
python -m cathy
```

进入 REPL 后即可对话，例如：

```
你 > 搜索一下今天关于 Anthropic 的新闻并用中文总结成 3 条
你 > 把 ROADMAP.md 第 1 节读出来
你 > 现在几点
你 > 建模 3DoF 机械臂，先确定 DH 参数再建模，DH 参数参考 UR3e
```

退出：`quit` / `exit` / `q` / Ctrl+C。

## Robot SDK 建模

CathyAgent 内置 `robot_sdk` 插件与 `skills/robot_sdk/SKILL.md`，用于 Agent 直接编辑 `robot_model.py` 并编译导出仿真资产。

标准闭环：

```text
create_robot_template
  -> 编辑 robot_model.py（调用 robot_sdk API）
  -> probe_robot_model（结构复杂时）
  -> compile_robot_model
  -> 根据 checks / summary 修复，直到 ok=true
```

编译成功后会返回：

- `mjcf_path`：MuJoCo MJCF
- `urdf_path`：URDF
- `obj_paths`：mesh-backed visual 生成的 OBJ（默认在 `build/robot/assets/meshes/`）
- `report_path`：校验报告

机械臂建模默认会生成 OBJ visual assets；collision / inertial 仍建议使用简化 primitive。详细 API 与约定见 `robot_sdk/docs/`。

示例与模板：

- 模板：`plugins/builtin/robot_sdk/templates/`（`empty` / `two_link` / `three_dof_arm`）
- 示例：`examples/robot_sdk/`（双连杆、3DoF、SCARA、夹爪、mesh visual）

## 当前能力（Phase 5.1 · MCP + 权限治理）

| 模块 | 状态 |
|------|------|
| Single-loop ReAct 主循环 | ✅ |
| OpenAI 兼容 LLM 客户端 | ✅ |
| 多级 system prompt 装配（含 skill 目录） | ✅ |
| 声明式插件系统（`plugin.yaml` + `PluginRegistry`） | ✅ |
| 内置插件 `web_search` / `file_ops` / `current_datetime` | ✅ |
| 会话持久化（SQLite） + `--session` / `--list-sessions` | ✅ |
| Token 预算硬截断（user 边界对齐） | ✅ |
| **Skills**（静态模板 + `read_skill` 工具，progressive disclosure） | ✅ |
| **Subagent**：`planner_executor` 基于 LangGraph 的 plan-execute-replan | ✅ |
| **ToolView**（白/黑名单视图，限定子 agent 可用工具集） | ✅ |
| **Hooks 中间件**：8 类事件 + Python/Command 双后端，兼容 `.claude/settings.json` | ✅ |
| **MCP 客户端接入**（FastMCP，stdio/remote，自动注册到 PluginRegistry） | ✅ |
| **MCP roots 协商**（`file://` 规范化 + filesystem 参数自动推断） | ✅ |
| **MCP 工具命名**（`mcp__<server>__<tool>`，单 server 回退 `mcp__<tool>`） | ✅ |
| **MCP 权限治理**（`PERMISSION.mcp_rules`：deny/ask/allow） | ✅ |
| **Robot SDK**（`robot_model.py` 建模 + MJCF/URDF/OBJ 导出） | ✅ |
| iMessage / 远端 Linux Agent | 见 `ROADMAP.md`，后续 Phase 实现 |

## Skill 与 Subagent 是两件事

| 维度 | Skill（能力模板） | Subagent（子代理实例） |
|---|---|---|
| 是什么 | Markdown 文档（指令 + 约束 + 步骤） | 一个会跑的 agent，有自己的 turn loop |
| 在哪里 | `skills/<name>/SKILL.md` | `cathy/subagent/*.py` |
| 谁用 | 主 agent / 子 agent 都能加载 | 由父 agent 派任务 |
| 怎么用 | 调 `read_skill(name)` 读取全文，按指示行事 | 直接调对应工具（如 `planner_executor`） |
| 例子 | `summarize` / `write_blog` | `planner_executor` |

## 内置插件清单

| 插件 | 提供的 tools | 说明 |
|------|--------------|------|
| `web_search` | `web_search` | 联网搜索（Tavily） |
| `file_ops` | `read_file` / `list_dir` / `write_file` | 工作目录子树内的文件操作 |
| `current_datetime` | `get_current_datetime` | 系统时间，避免模型幻觉时间 |
| `skills` | `read_skill` | 按名拉取一份 SKILL.md 全文（progressive disclosure） |
| `robot_sdk` | `create_robot_template` / `compile_robot_model` / `probe_robot_model` | Robot SDK 建模、校验、MJCF/URDF/OBJ 导出 |
| `planner_executor` | `planner_executor` | LangGraph 实现的 plan-execute-replan 子 agent |
| `mcp`（运行时注入） | `mcp__<server>__<tool>` | 外部 MCP 生态工具（FastMCP Client 聚合） |

## MCP 与权限治理（Phase 5.1）

- MCP 启动入口在 `config/config.yaml` 的 `MCP` 段，支持本地 stdio 与远端服务。
- roots 语义：filesystem server 会请求 client roots，当前实现会把路径规范为 `file://...` URI，避免 `url_parsing` 报错。
- 工具名统一为 `mcp__<server>__<tool>`，方便按 server 维度写 hook 与权限规则。
- `HOOKS` 的 matcher 现支持 glob（`* ? []`），可写 `mcp__fs__write_*` 这类策略。
- `permission_gate` 支持 `PERMISSION.mcp_rules`，优先级为 `deny -> ask -> allow`，仅对 `mcp__*` 生效。

最小配置示例：

```yaml
MCP:
  enabled: true
  mcp_servers:
    fs:
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]

HOOKS:
  PreToolUse:
    - hooks:
        - type: python
          target: "cathy.hooks.builtin:permission_gate"
    - matcher: "write_file|mcp__fs__write_*"
      hooks:
        - type: python
          target: "cathy.hooks.builtin:block_dangerous_paths"

PERMISSION:
  trust_policy:
    builtin: allow
    verified: audit
    untrusted: ask
  mcp_rules:
    deny: ["mcp__fs__delete_*"]
    ask: ["mcp__fs__write_*"]
    allow: ["mcp__fs__read_*", "mcp__memory__*", "mcp__time__*"]
```

## Skill 工作机制

1. 启动时扫描 `skills/<name>/SKILL.md`，把 (name + 一句描述) 拼成目录注入主 agent system prompt。
2. agent 看到任务匹配某个 skill，调 `read_skill(name=...)` 拿到全文。
3. agent 按全文里的格式与步骤产出。**主 agent 自己也能用 skill**，无需必须派给 subagent。

示例 skill：
- `skills/summarize/SKILL.md` —— 三段式中文摘要
- `skills/write_blog/SKILL.md` —— 中文技术博客写作
- `skills/robot_sdk/SKILL.md` —— Robot SDK 建模工作流（索引 `robot_sdk/docs/`）

## Subagent 工作机制（planner_executor）

LangGraph StateGraph：

```
START → planner ──→ executor ──┬─── (plan 仍有步骤) ──→ executor
                                │
                                └─── (plan 已空) ────→ replanner ─┬→ executor (continue)
                                                                  └→ END (finish)
```

- **planner**：把 goal 拆成 3-7 步执行计划。
- **executor**：每个步骤跑一次内部 ReAct 子循环（带工具）。
- **replanner**：基于 past_steps 决定 finish（产出最终答案）或 continue（追加新 plan ≤ 4 步）。
- **隔离保证**：子 agent 不看父 session，最终只把一个 `final_answer` 字符串作为 tool_result 回传。

主 agent 何时该派给 `planner_executor`：任务**复杂、多步、中间产物长**；
简单单步任务直接 ReAct 完成即可，不要无脑派出。

## Hooks 中间件（Phase 3.5）

8 类事件埋点 + Python/Command 双后端；配置形态与 Claude Code 的 `.claude/settings.json` 完全一致，
项目根若放了 `.claude/settings.json`，会自动 merge 到 `config.yaml.HOOKS` 之后。

> 默认运行档位是 `HOOKS_PROFILE: mvp`，只启用 3 个事件：`UserPromptSubmit` / `PreToolUse` / `PostToolUse`。  
> 若要启用全部 8 类事件，把 `config/config.yaml` 里的 `HOOKS_PROFILE` 改成 `full`。

| 事件 | 埋点 | 常用改写 |
|------|------|---------|
| `SessionStart`     | `cli.main` 拿到 session 之后        | `inject_context` 写入 system prompt 末尾 |
| `UserPromptSubmit` | `Agent.run` 入口、user 持久化前     | `rewrite_user_input` / `block` / `inject_context` |
| `PreToolUse`       | `tools.call()` 之前                 | `rewrite_params` / `block`（结构化错误回灌让 LLM 自纠） |
| `PostToolUse`      | `tools.call()` 之后                 | `inject_context` 拼到 tool result 末尾 |
| `Stop`             | final 分支 return 之前              | `rewrite_final_answer` / `block`（强制再循环） |
| `SubagentStop`     | `SubagentToolPlugin.execute` 之后   | `rewrite_final_answer` |
| `PreCompact`       | `ContextAssembler._fit_to_budget`   | 仅观测（MVP 不接受改写） |
| `Notification`     | 任意位置主动调                      | 路由到外部（IM / 邮件） |

### MVP 默认 hook 组合（`HOOKS_PROFILE: mvp`）

| Hook | 事件 | 作用 |
|------|------|------|
| `cathy.hooks.builtin:strip_secrets`        | `UserPromptSubmit` | 用 regex 把 `sk-...` / `ghp_...` / `api_key=...` 等替换为 `[REDACTED]` |
| `cathy.hooks.builtin:permission_gate`      | `PreToolUse` | 按 `trust_policy` + `mcp_rules` 执行 allow/audit/ask/deny |
| `cathy.hooks.builtin:block_dangerous_paths`| `PreToolUse`/`write_file|mcp__fs__write_*` | 拦截写入 `/etc` / `~/.ssh` / `~/.aws` 等敏感路径 |
| `cathy.hooks.builtin:block_dangerous_shell_commands`| `PreToolUse`/`shell_exec|mcp__*__exec_*` | 拦截 `rm -rf /` / `sudo` / fork bomb 等高危命令 |
| `cathy.hooks.builtin:audit_log`            | `PostToolUse` | 每次 tool call 写一行到 `data/audit.jsonl` |

### 自定义 hook（两种方式）

**Python（推荐，零开销，类型化）：**

```python
# plugins/community/my_audit/hook.py
from cathy.hooks import HookDecision, HookEvent

def my_post_tool(event: HookEvent) -> HookDecision:
    if (event.payload or {}).get("tool") == "web_search":
        return HookDecision(inject_context="提示：来自 web_search 的内容请优先核对来源")
    return HookDecision()
```

```yaml
# config/config.yaml
HOOKS:
  PostToolUse:
    - hooks:
        - { type: python, target: "plugins.community.my_audit.hook:my_post_tool" }
```

**Command（兼容 Claude Code 已有脚本）：**

```yaml
HOOKS:
  PreToolUse:
    - matcher: "write_file"
      hooks:
        - type: command
          command: "python tools/cc_hook.py"   # stdin=event.json，stdout=decision.json，exit 2 = block
          timeout: 5
```

## 添加自己的插件

1. 在 `plugins/community/<name>/` 创建目录
2. 写 `plugin.yaml`（参考 `plugins/builtin/web_search/plugin.yaml`）
3. 写 `main.py`，实现 `ToolPlugin` 子类
4. 重启即生效；schema 不合规会被自动拒绝并打印原因

## 目录结构

```text
CathyAgent/
  config/
    .env                      # 密钥（gitignored）
    config.yaml               # LLM_API / TAVILY_API_KEY / AGENT / SESSION
    config.py                 # 配置加载器
  cathy/
    agent.py                  # ReAct 主循环（session-aware，接 4 类 hook）
    context.py                # 多级 system prompt 装配 + 预算截断 + PreCompact hook
    llm.py                    # OpenAI 兼容客户端
    cli.py                    # REPL 入口（构造 HookManager + 触发 SessionStart）
    plugins/
      base.py                 # ToolPlugin 接口
      manifest.py             # plugin.yaml 解析
      registry.py             # PluginRegistry + ToolView
    session/                  # Session / Message + SQLite 持久化
    skills/                   # 静态模板子系统
      manifest.py             # SKILL.md 解析
      loader.py               # discover + 目录字符串
      plugin.py               # SkillsPlugin（read_skill 工具）
    subagent/                 # 运行实体子系统
      base.py                 # Subagent / SubagentResult 抽象
      runner.py               # SubagentRunner（朴素 ReAct 子循环，executor 复用）
      planner_executor.py     # LangGraph plan-execute-replan
      tool_plugin.py          # 把 Subagent 包装成 ToolPlugin（接 SubagentStop）
    hooks/                    # 【新 Phase 3.5】中间件子系统
      events.py               # HookEvent / HookDecision / 8 事件常量 / merge
      runners.py              # PythonRunner + CommandRunner
      manager.py              # HookManager：matcher / 串联 / .claude 兼容
      builtin.py              # audit_log / block_dangerous_paths / strip_secrets
    mcp/                      # 【新 Phase 5】MCP 客户端聚合层
      client.py               # McpHub（FastMCP Client 后台 loop，同步桥接）
      plugin.py               # McpToolPlugin + build_mcp_manifest
      __init__.py
  plugins/
    builtin/                  # web_search / file_ops / current_datetime / robot_sdk
    community/                # 用户插件
  robot_sdk/                  # Robot SDK 核心库 + docs/
    docs/                     # concepts / robot_arms / assets / export / troubleshooting
    model.py                  # RobotModel / Link / Joint / Actuator ...
    assets.py                 # AssetSession / mesh_from_vertices / mesh_from_cadquery
    mjcf_export.py            # MJCF 导出
    urdf_export.py            # URDF 导出
  examples/
    robot_sdk/                # 机械臂 / 夹爪 / mesh visual 示例
  skills/
    summarize/SKILL.md
    write_blog/SKILL.md
    robot_sdk/SKILL.md
  data/                       # SQLite db（gitignored）
  tests/                      # unittest 套件
  main.py                     # python main.py 入口
  requirements.txt
  ROADMAP.md
  ARCHITECTURE.md
```
