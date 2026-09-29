# CathyAgent

[中文文档](docs/README_zh.md)

CathyAgent is a local agent harness kernel for tool-using AI systems. It supports general ReAct workflows and an embodied manipulation runtime where an agent can observe and control a Unitree Z1 robot in MuJoCo through structured tools.

## MuJoCo Manipulation Demo

### Unitree Z1 three-cube pick-and-place

![Unitree Z1 performing three-cube pick-and-place in MuJoCo](simulation/mujoco/docs/images/z1-three-cube-viewer.png)

### Natural-language robot control

![CathyAgent completing a red-cube pick-and-place instruction](simulation/mujoco/docs/images/cathyagent-mujoco-cli.jpg)

The manipulation runtime is agent-driven: CathyAgent owns the multi-turn context and calls MuJoCo tools for environment discovery, RGB observation, end-effector motion, gripper control, and simulation-time advancement. See the [MuJoCo runtime documentation](simulation/mujoco/README.md) for architecture and configuration details.

## Quick Start

### 1. Install dependencies

```bash
conda activate agent   # or your own Python 3.10+ environment
pip install -r requirements.txt
```

### 2. Configure secrets

`config/.env` can hold local API keys:

```env
DEEPSEEK_API_KEY=sk-...
OPENAI_API_KEY=sk-...
QWEN_API_KEY=sk-...
TAVILY_API_KEY=tvly-...
```

Model settings live in `config/config.yaml`; provider-specific LLM configs live in `config/llm/<provider>.yaml`. The base system prompt is assembled in `cathy/context.py`, and project-level rules can be placed in `AGENTS.md` or `CATHY.md`.

### 3. Run

```bash
python main.py
# or
python -m cathy
```

Inside the REPL:

```text
你 > 搜索一下今天关于 Anthropic 的新闻并用中文总结成 3 条
你 > 把 docs/ROADMAP.md 第 1 节读出来
你 > 现在几点
```

Exit with `quit`, `exit`, `q`, or Ctrl+C.

### 4. Run the MuJoCo manipulation agent

Install the optional simulation dependencies:

```bash
pip install -r simulation/mujoco/requirements.txt
```

On macOS, launch the agent with `mjpython` so the passive Viewer can run on the UI thread:

```bash
mjpython -m simulation.mujoco --agent
```

Example instruction:

```text
你 > 把红色小方块放到盒子中
```

Use `--no-viewer` for CI or headless execution:

```bash
python -m simulation.mujoco --agent --no-viewer
```

## Capabilities

| Module | Status |
|---|---|
| Single-loop ReAct agent loop | Done |
| OpenAI-compatible LLM client, including `max_tokens` / `max_completion_tokens` compatibility | Done |
| Multi-layer system prompt assembly, including tool catalog and skill catalog | Done |
| Declarative plugin system with `plugin.yaml` and `PluginRegistry` | Done |
| Built-in plugins: `web_search`, `file_ops`, `current_datetime` | Done |
| `web_search` hidden behind `search_agent` for the main agent | Done |
| SQLite session persistence with `--session` and `--list-sessions` | Done |
| Token-budget truncation aligned to user-message boundaries | Done |
| Skills with progressive disclosure through `read_skill` | Done |
| LangGraph subagents: `search_agent` and `planner_executor` | Done |
| `ToolView` allow/block views for parent agents and subagents | Done |
| Central logging via `cathy/logger.py`, writing to project-root `log/` | Done |
| Hooks middleware with Python and command runners | Done |
| MCP client integration through FastMCP | Done |
| MCP roots negotiation and `mcp__<server>__<tool>` naming | Done |
| MCP permission rules through `PERMISSION.mcp_rules` | Done |
| Agent-driven MuJoCo manipulation runtime | Done |
| Unitree Z1 three-cube physical pick-and-place environment | Done |
| Multimodal RGB observations with content-addressed artifacts | Done |
| Passive MuJoCo Viewer with lock-safe state synchronization | Done |
| Streaming model responses, tool calls, and tool results | Done |
| Privileged evaluation state isolated from agent tools | Done |

## MuJoCo Manipulation Runtime

```text
User instruction
  -> CathyAgent multi-turn loop
     -> robot_describe / robot_observe / robot_move_ee
        / robot_set_gripper / robot_wait
        -> MujocoToolPlugin
           -> MujocoRuntime (queue + exclusive state thread)
              -> Backend + Z1 controller + RGB observations + Viewer
```

Key properties:

- CathyAgent drives the interaction loop; MuJoCo does not call the agent.
- VLMs produce one end-effector target at a time rather than an action chunk.
- `robot_describe` exposes frames, workspace bounds, cameras, units, and controller capabilities dynamically.
- RGB images are persisted as SHA-256 artifacts and can be returned to multimodal models.
- The passive Viewer opens with the runtime and synchronizes after every control step.
- MuJoCo `model/data` remain on one exclusive thread; tool calls use an asynchronous command queue.
- Object ground truth is available only to the physical regression evaluator, not to the agent.

The default scene, controller, observation, assets, tests, screenshots, and outputs are self-contained under [`simulation/mujoco/`](simulation/mujoco/README.md).

## Skills vs Subagents

| Dimension | Skill | Subagent |
|---|---|---|
| What it is | A Markdown instruction template | A runnable agent with its own execution loop or state graph |
| Location | `skills/<name>/SKILL.md` | `subagents/<name>/agent.py` |
| Who uses it | Main agent and subagents | Called by a parent agent as a tool |
| How it is used | Call `read_skill(name)` to load full instructions | Call the exposed tool, such as `search_agent` or `planner_executor` |
| Examples | `summarize`, `write_blog` | `search_agent`, `planner_executor` |

## Tools and Plugins

| Provider | Tools | Notes |
|---|---|---|
| `web_search` | `web_search` | Tavily web search. It is available internally to `search_agent`, not directly exposed to the main agent by default. |
| `file_ops` | `read_file`, `list_dir`, `write_file` | Workspace-scoped file operations. |
| `current_datetime` | `get_current_datetime` | Reads system date/time to avoid time hallucinations. |
| `skills` | `read_skill` | Loads full `SKILL.md` content on demand. |
| `search_agent` | `search_agent` | Expands queries, runs concurrent web searches, filters relevant pages. |
| `planner_executor` | `planner_executor` | LangGraph plan-execute-replan subagent. |
| `mcp` | `mcp__<server>__<tool>` | Runtime MCP tools aggregated by FastMCP. |
| `mujoco` | `robot_describe`, `robot_observe`, `robot_move_ee`, `robot_set_gripper`, `robot_wait` | Dedicated Z1 manipulation runtime with RGB artifacts and passive Viewer. |

The main agent receives a filtered `ToolView`. In the default runtime, raw `web_search` is blocked from the main agent and from `planner_executor`; both should use `search_agent` for web research.

## Subagents

Concrete subagents live under project-root `subagents/<name>/agent.py`. The framework layer remains in `cathy/subagent/` and contains `Subagent`, `SubagentResult`, the generic runner, and the `SubagentToolPlugin` adapter.

### `search_agent`

`search_agent` wraps web search as a LangGraph state machine:

```text
START -> expand_queries -> search_queries -> select_relevant -> format_answer -> END
```

- `expand_queries`: asks the LLM to rewrite the user question into a query group.
- `search_queries`: runs the query group concurrently through internal `web_search`.
- `select_relevant`: asks the LLM to select relevant pages from titles and summaries.
- `format_answer`: returns selected pages, matching queries, and summaries to the parent agent.

### `planner_executor`

`planner_executor` is a LangGraph plan-execute-replan subagent:

```text
START -> planner -> executor -> replanner -> END
              ^        |
              |        |
              +--------+
```

- `planner`: decomposes a goal into 3-7 executable steps.
- `executor`: runs a focused ReAct loop for one step at a time.
- `replanner`: decides whether to finish or continue with a revised short plan.

Use `planner_executor` for complex, multi-step tasks with substantial intermediate state. Simple one-step tasks should stay in the main ReAct loop.

### Context Isolation

- The main agent uses `ContextAssembler + SessionStore` and persists user, assistant, tool-call, and tool-result messages.
- Subagents do not inherit the parent session history.
- Parent agents must pass complete background in tool parameters, such as `search_agent.question` or `planner_executor.goal`.
- A subagent returns one `final_answer`, which becomes the tool result seen by the parent agent.

## Skills

At startup, CathyAgent scans `skills/<name>/SKILL.md` and injects only `(name + description)` into the system prompt. When a task matches a skill, the agent calls `read_skill(name=...)` to load the full instruction document.

Example skills:

- `skills/summarize/SKILL.md`
- `skills/write_blog/SKILL.md`

## MCP and Permissions

MCP servers are configured in `config/config.yaml` under `MCP.mcp_servers`. Local stdio and remote servers are supported.

```yaml
MCP:
  enabled: true
  mcp_servers:
    fs:
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
```

MCP tools are exposed as `mcp__<server>__<tool>`, which makes hook matching and permission rules easier to write.

```yaml
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

## Hooks

Hooks provide middleware-style interception around agent events. The default `HOOKS_PROFILE: mvp` enables:

- `UserPromptSubmit`
- `PreToolUse`
- `PostToolUse`

The full event set includes:

| Event | Location | Common use |
|---|---|---|
| `SessionStart` | CLI after session creation | Inject context into system prompt |
| `UserPromptSubmit` | Before persisting user input | Rewrite, block, or inject context |
| `PreToolUse` | Before tool execution | Rewrite params or block |
| `PostToolUse` | After tool execution | Append context to tool result |
| `Stop` | Before final answer | Rewrite or force regeneration |
| `SubagentStop` | After subagent execution | Rewrite subagent final answer |
| `PreCompact` | Before history compaction | Observability |
| `Notification` | Explicit dispatch points | External routing |

## Logging

- Central logging lives in `cathy/logger.py`.
- REPL dialogue uses `print`; runtime logs use `logger`.
- Log files are written to project-root `log/`.
- `log/` is ignored by Git.

## Add a Plugin

1. Create `plugins/community/<name>/`.
2. Add `plugin.yaml` with tool schemas.
3. Add `main.py` implementing a `ToolPlugin` subclass.
4. Restart CathyAgent. Invalid schemas are rejected during startup.

## Project Layout

```text
CathyAgent/
  config/
    .env                      # Local secrets, gitignored
    config.yaml               # Runtime config
    config.py                 # Config loader
    llm/                      # Provider-specific LLM configs
  cathy/
    agent.py                  # Session-aware ReAct loop
    context.py                # System prompt assembly and history budget
    llm.py                    # OpenAI-compatible client
    logger.py                 # Central logger config
    cli.py                    # REPL entrypoint
    plugins/                  # Plugin framework
    session/                  # Session and SQLite persistence
    skills/                   # Skill loader and read_skill tool
    subagent/                 # Subagent framework layer
    hooks/                    # Hook middleware
    mcp/                      # MCP client aggregation
  plugins/
    builtin/                  # Built-in tools
    community/                # User plugins
  subagents/
    search_agent/
      agent.py
    planner_executor/
      agent.py
  skills/
    summarize/SKILL.md
    write_blog/SKILL.md
  simulation/
    contracts.py              # Simulator-independent runtime contracts
    mujoco/
      configs/                # Environment, robot, controller, observation
      assets/                 # Z1 MJCF, meshes, and pick-place scene
      docs/images/            # README demo screenshots
      tests/                  # Runtime and physical regression tests
      runtime.py              # Exclusive MuJoCo state thread
      plugin.py               # CathyAgent robot tools
      viewer.py               # Passive Viewer lifecycle and synchronization
  docs/
    README_zh.md
    ARCHITECTURE.md
    ROADMAP.md
  data/                       # SQLite DB, gitignored
  log/                        # Runtime logs, gitignored
  tests/
  main.py
  requirements.txt
```
