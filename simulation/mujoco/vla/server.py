"""与本地仿真同生命周期的内存 MCP 服务，不开放额外公网端口。"""
import base64
import json


def create_skill_server(executor):
    from fastmcp import FastMCP
    from fastmcp.tools.tool import ToolResult
    from mcp.types import ImageContent, TextContent

    server = FastMCP("CathyLocalRobot")

    async def observation_result(report=None):
        async with executor.lock:
            state, images = await executor.runtime.snapshot()
        payload = {"observation": state}
        if report is not None:
            payload["execution"] = report
        content = [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]
        for camera, png in images:
            content.append(TextContent(type="text", text=f"camera={camera}; snapshot={state['snapshot_id']}"))
            content.append(ImageContent(type="image", mimeType="image/png",
                                        data=base64.b64encode(png).decode("ascii")))
        return ToolResult(content=content, structured_content=payload)

    @server.tool(name="robot_vla_robot_describe")
    def robot_describe() -> dict:
        """查询当前机器人场景、相机、已启用控制模式和策略兼容性。"""
        return executor.runtime.describe()

    @server.tool(name="robot_vla_robot_observe")
    async def robot_observe() -> ToolResult:
        """获取本地同一 MuJoCo 环境的三路相机图像和本体状态，不返回物体真值。"""
        return await observation_result()

    @server.tool(name="robot_vla_robot_execute_skill")
    async def robot_execute_skill(instruction: str, arm: str | None = None,
                                  max_policy_steps: int | None = None,
                                  request_id: str | None = None) -> ToolResult:
        """用 π0.5 执行一个自然语言子任务，返回执行统计与最新相机帧。

        宿主自动调用对应的 AutoDL π 推理工具并执行动作，禁止传入坐标或动作数组。
        step_budget_reached 仅表示本轮执行结束；必须观察返回图像验证任务是否成功。
        arm 必须来自 robot_describe 的 allowed_arms；ALOHA 为 both，旧 Panda 为 left/right。
        同进程内相同 request_id 不重复执行。支持14维双臂接口不代表模型任务表现已验证。
        """
        report = await executor.execute(instruction, arm, max_policy_steps, request_id)
        return await observation_result(report)

    return server
