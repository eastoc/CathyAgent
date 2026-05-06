# CathyAgent

借鉴 Claude Code 设计哲学的本地 Agent harness。详细架构见 `ARCHITECTURE.md`，分阶段路线图见 `ROADMAP.md`。

## Phase 0 快速开始

### 1. 安装依赖

```bash
conda activate agent   # 或你自己的 Python 3.10+ 环境
pip install -r requirements.txt
```

### 2. 配置密钥

`config/.env` 已包含示例 key（仅本地开发，已被 `.gitignore` 忽略）。如需替换，修改：

```env
QWEN_API_KEY=sk-...
TAVILY_API_KEY=tvly-...
```

模型与系统提示词可在 `config/config.yaml` 中调整。

### 3. 运行

任选其一：

```bash
python main.py
# 或
python -m cathy
```

进入 REPL 后即可对话。例如：

```
你 > 搜索一下今天关于 Anthropic 的新闻并用中文总结成 3 条
你 > 把 ROADMAP.md 第 1 节读出来
```

退出：`quit` / `exit` / `q` / Ctrl+C。

## 当前能力（Phase 0）

| 模块 | 状态 |
|------|------|
| Single-loop ReAct 主循环 | ✅ |
| OpenAI 兼容 LLM 客户端 | ✅ |
| 工具注册表（极简版） | ✅ |
| `web_search`（Tavily） | ✅ |
| `read_file`（cwd 子树受限） | ✅ |
| 配置加载（YAML + ${ENV} + .env） | ✅ |
| Plugin Manifest / Subagent / Sandbox / Memory / iMessage | 见 `ROADMAP.md`，后续 Phase 实现 |

## 目录结构

```text
CathyAgent/
  config/
    .env                # 密钥（gitignored）
    config.yaml         # LLM_API + AGENT + TAVILY_API_KEY
    config.py           # 配置加载器
  cathy/
    agent.py            # ReAct 主循环
    llm.py              # OpenAI 兼容客户端
    cli.py              # REPL 入口
    __main__.py         # python -m cathy
    tools/
      base.py           # Tool 基类 + ToolRegistry
      web_search.py
      read_file.py
  main.py               # python main.py 入口
  requirements.txt
  ROADMAP.md
  ARCHITECTURE.md
```
