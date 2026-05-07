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
QWEN_API_KEY=sk-...
TAVILY_API_KEY=tvly-...
```

模型参数可在 `config/config.yaml` 中调整；系统提示词在 `cathy/context.py`；项目级规则可写入项目根的 `AGENTS.md` 或 `CATHY.md`，会自动注入。

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
```

退出：`quit` / `exit` / `q` / Ctrl+C。

## 当前能力（Phase 3 · Skill / Subagent 分离版）

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
| Sandbox / Memory / MCP / iMessage | 见 `ROADMAP.md`，后续 Phase 实现 |

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
| `planner_executor` | `planner_executor` | LangGraph 实现的 plan-execute-replan 子 agent |

## Skill 工作机制

1. 启动时扫描 `skills/<name>/SKILL.md`，把 (name + 一句描述) 拼成目录注入主 agent system prompt。
2. agent 看到任务匹配某个 skill，调 `read_skill(name=...)` 拿到全文。
3. agent 按全文里的格式与步骤产出。**主 agent 自己也能用 skill**，无需必须派给 subagent。

示例 skill：
- `skills/summarize/SKILL.md` —— 三段式中文摘要
- `skills/write_blog/SKILL.md` —— 中文技术博客写作

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
    agent.py                  # ReAct 主循环（session-aware）
    context.py                # 多级 system prompt 装配 + 预算截断 + skill 目录注入
    llm.py                    # OpenAI 兼容客户端
    cli.py                    # REPL 入口
    plugins/
      base.py                 # ToolPlugin 接口
      manifest.py             # plugin.yaml 解析
      registry.py             # PluginRegistry + ToolView
    session/                  # Session / Message + SQLite 持久化
    skills/                   # 【新】静态模板子系统
      manifest.py             # SKILL.md 解析
      loader.py               # discover + 目录字符串
      plugin.py               # SkillsPlugin（read_skill 工具）
    subagent/                 # 【新】运行实体子系统
      base.py                 # Subagent / SubagentResult 抽象
      runner.py               # SubagentRunner（朴素 ReAct 子循环，executor 复用）
      planner_executor.py     # LangGraph plan-execute-replan
      tool_plugin.py          # 把 Subagent 包装成 ToolPlugin
  plugins/
    builtin/                  # web_search / file_ops / current_datetime
    community/                # 用户插件
  skills/
    summarize/SKILL.md
    write_blog/SKILL.md
  data/                       # SQLite db（gitignored）
  tests/                      # unittest 套件
  main.py                     # python main.py 入口
  requirements.txt
  ROADMAP.md
  ARCHITECTURE.md
```
