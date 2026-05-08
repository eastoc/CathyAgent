# LangGraph 与 AutoGen 对比调研报告

## 一、核心抽象对比

### LangGraph：节点/边/状态
LangGraph 基于有向图模型构建多智能体工作流。**节点（Node）**代表独立的功能单元（如 LLM 调用、工具执行）；**边（Edge）**定义节点间的流转逻辑，支持条件分支与循环；**状态（State）**是贯穿全局的共享数据对象，各节点可读写更新，实现跨步骤的记忆与上下文传递。这种设计适合需要精确控制执行流程的场景。

> 权威来源：[LangGraph 官方文档](https://langchain-ai.github.io/langgraph/) | [GitHub 仓库](https://github.com/langchain-ai/langgraph)

### AutoGen：Agent/Group
AutoGen 以**智能体（Agent）**为核心，每个 Agent 是独立软件实体，通过消息通信、维护自身状态、执行动作（如代码执行、API 调用）。**群组（Group）**是多智能体协作模式，由 `GroupChat` 管理参与者列表，`GroupChatManager` 控制发言顺序与流程，实现去中心化的对话式协作。

> 权威来源：[AutoGen 官方文档](https://microsoft.github.io/autogen/) | [GitHub 仓库](https://github.com/microsoft/autogen)

---

## 二、使用场景

### LangGraph 最适合的 3 个场景
1. **客户支持机器人** — 利用状态管理处理多轮对话与复杂查询流转
2. **编程助手** — 通过节点/边编排实现代码生成、审查、迭代的有向工作流
3. **研究自动化** — 适合需要记忆状态、条件分支和工具调用的复杂推理任务

> 来源：[LangGraph 示例仓库](https://github.com/langchain-ai/langgraph/tree/main/examples)

### AutoGen 最适合的 3 个场景
1. **多智能体代码协作** — 多角色分工（写代码/调试/可视化），支持代码执行与错误恢复
2. **数据模型生成** — 从数据库架构自动生成数据模型（如 Entity Framework Core）
3. **API 集成与容错服务** — 多智能体协作调用外部 API，具备自动重试和错误恢复能力

> 来源：[AutoGen 官方示例](https://microsoft.github.io/autogen/docs/examples/) | [E2B 案例分析](https://e2b.dev/blog/microsoft-s-autogen)

---

## 三、总结对比

| 维度 | LangGraph | AutoGen |
|------|-----------|---------|
| **架构设计** | 有向图模型（节点/边），流程显式可控 | 消息驱动模型（Agent/Group），对话式协作 |
| **状态管理** | 全局共享状态对象，节点间显式传递与更新 | 各 Agent 维护独立状态，通过消息隐式同步 |
| **适用场景** | 需要精确流程控制、循环与条件分支的任务 | 多角色对话协作、代码执行与自动容错场景 |
| **学习曲线** | 中等，需理解图结构与状态流转逻辑 | 较低，基于自然对话范式，易上手 |
| **扩展性** | 高，支持自定义节点、边逻辑与状态 schema | 高，支持自定义 Agent 类型与 Group 管理策略 |
| **代码执行** | 需手动集成工具节点 | 内置代码执行器，支持多语言沙箱运行 |
| **生态依赖** | 深度集成 LangChain 生态 | 独立框架，可与任意 LLM 提供商对接 |

> 综合参考：[LangGraph 文档](https://langchain-ai.github.io/langgraph/) | [AutoGen 文档](https://microsoft.github.io/autogen/)
