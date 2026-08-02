"""
Agent Idle/挂机模式使用示例

展示如何启动支持WORK/IDLE两阶段的主Agent循环，以及如何从外部触发Agent。

使用场景：
1. 用户不活跃时，Agent自动进入idle状态节省资源
2. 股价异常时，自动触发Agent进行分析和通知
3. 用户发送消息时，Agent从idle恢复处理
"""
import asyncio
from core.agent import AStockAgent
from services.monitoring_service import monitoring_service


async def demo_idle_mode():
    """
    演示Idle模式的使用
    """
    # 创建Agent实例
    agent = AStockAgent(session_id="demo")

    # 设置消息处理器（用于CLI输出）
    def on_message(msg: str):
        print(f"\n[Agent回复] {msg}\n")
    agent.set_message_handler(on_message)

    # ========== 方式1: 启动Agent主循环（推荐） ==========
    # 启动后Agent会在WORK和IDLE之间自动切换
    await agent.start()

    print("✅ Agent主循环已启动，当前状态:", agent.status)

    # 模拟用户发送消息（通过事件队列触发Agent工作）
    await asyncio.sleep(2)
    print("📤 模拟用户发送消息...")
    await agent.post_event("user_message", "分析一下贵州茅台")

    await asyncio.sleep(5)

    # ========== 方式2: 股价异常触发Agent ==========
    print("🚨 模拟股价异常事件...")
    await agent.post_event(
        "price_alert",
        "股票600036触发价格异动，涨跌幅+5.2%",
        {"code": "600036", "change": 5.2, "level": "high"}
    )

    await asyncio.sleep(5)

    # ========== 方式3: 兼容模式（不使用主循环） ==========
    # 如果agent.start()没有被调用，chat()方法会同步处理
    # response = await agent.chat("查询平安银行股价")
    # print(response)

    # 运行一段时间后停止
    await asyncio.sleep(10)
    print("⏹️ 停止Agent...")
    await agent.stop()
    print("✅ Agent已停止，最终状态:", agent.status)


async def demo_with_monitoring():
    """
    演示Agent与监控服务集成

    监控服务检测到异常时，自动触发Agent进行深度分析
    """
    agent = AStockAgent(session_id="monitoring_demo")
    await agent.start()

    # 设置监控服务
    monitoring_service.set_agent(agent)

    # 添加自选股
    monitoring_service.add_stock("600036")  # 招商银行
    monitoring_service.add_stock("000001")  # 平安银行

    # 启动监控服务
    await monitoring_service.start()

    print("🚀 系统和监控服务已启动")
    print("- 监控服务每分钟检查股价")
    print("- 发现异常时自动触发Agent分析")
    print("- Agent分析完成后自动进入idle等待")

    try:
        # 运行一段时间
        await asyncio.sleep(300)  # 运行5分钟
    except KeyboardInterrupt:
        pass

    # 停止服务
    monitoring_service.stop()
    await agent.stop()


async def demo_cli_mode():
    """
    CLI交互模式示例

    在CLI模式下，用户输入触发Agent工作，完成后进入idle
    """
    agent = AStockAgent(session_id="cli")

    # 设置消息处理器
    def on_message(msg: str):
        print(f"\n🤖 {msg}\n")
    agent.set_message_handler(on_message)

    # 启动Agent主循环
    await agent.start()

    print("=" * 50)
    print("AStock Agent CLI (Idle模式)")
    print("输入消息与Agent对话，输入 'exit' 退出")
    print("=" * 50)

    while True:
        try:
            user_input = input("\n👤 你: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "退出"):
                break

            # 通过事件队列发送消息
            await agent.post_event("user_message", user_input)

            # 等待Agent处理（简单等待，实际可以使用Future机制）
            await asyncio.sleep(1)

        except KeyboardInterrupt:
            break

    print("\n👋 再见！")
    await agent.stop()


if __name__ == "__main__":
    # 运行演示
    print("选择演示模式:")
    print("1. Idle模式基础演示")
    print("2. 集成监控服务演示")
    print("3. CLI交互模式")

    choice = input("\n选择 (1-3): ").strip()

    if choice == "1":
        asyncio.run(demo_idle_mode())
    elif choice == "2":
        asyncio.run(demo_with_monitoring())
    elif choice == "3":
        asyncio.run(demo_cli_mode())
    else:
        print("无效选择")
