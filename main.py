"""
A股分析Agent系统 - 主入口

功能:
1. 开市期间持续监控自选股票技术指标异常
2. 每天开市前生成市场情绪和消息面分析报告
3. 对话交互分析股票
4. 维护用户长期记忆

使用方法:
    python main.py              # 启动完整服务
    python main.py --cli        # 仅启动命令行交互
    python main.py --report     # 仅生成一次报告
    python main.py --monitor    # 仅启动监控服务
"""

import asyncio
import argparse
import logging
import sys
from pathlib import Path

# 添加项目目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from config import config
from core.agent import AStockAgent
from core.memory import user_memory
from core.scheduler import TaskScheduler
from core.notifier import notification_manager
from services.monitoring_service import monitoring_service
from services.morning_report_service import morning_report_service
from services.screening_service import screening_service
from channels import wecom_channel, feishu_channel


# 设置日志
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOG_FILE, encoding='utf-8') if config.LOG_FILE else logging.NullHandler()
    ]
)

logger = logging.getLogger(__name__)


class AStockSystem:
    """A股分析系统主类"""

    def __init__(self, session_id: str = "default"):
        self.agent = AStockAgent(session_id=session_id)
        self.scheduler = TaskScheduler(self.agent)
        self.running = False

    async def start(self, mode: str = "full"):
        """
        启动系统

        Args:
            mode: 启动模式 (full, cli, report, monitor)
        """
        self.running = True

        print("""
    ╔═══════════════════════════════════════════════════════════╗
    ║                                                           ║
    ║              📈 A股分析Agent系统 v1.0                      ║
    ║                                                           ║
    ║   功能:                                                   ║
    ║   • 实时监控自选股票技术指标异常                           ║
    ║   • 每日开市前生成市场分析报告                             ║
    ║   • 对话式股票分析和问答                                   ║
    ║   • 智能选股和推荐                                         ║
    ║                                                           ║
    ╚═══════════════════════════════════════════════════════════╝
        """)

        logger.info(f"🚀 启动A股分析系统 (模式: {mode})")

        # 加载用户记忆
        await self._load_user_memory()

        # 启动外部渠道服务器（企业微信、飞书等，如果配置）
        await self._start_channel_servers()

        if mode == "full":
            await self._start_full()
        elif mode == "cli":
            await self._start_cli()
        elif mode == "report":
            await self._generate_report()
        elif mode == "monitor":
            await self._start_monitor()

    async def _load_user_memory(self):
        """加载用户记忆，如需要则引导初始化"""
        # 检查是否需要初始化
        if user_memory.needs_initialization():
            logger.info("🆕 检测到新用户，启动初始化流程")
            await self._initialize_user_profile()
        else:
            context = user_memory.get_context()
            logger.info("📚 已加载用户记忆")
            print(f"\n{context}\n")

    async def _initialize_user_profile(self):
        """引导用户完成初始化设置"""
        print("""
┌─────────────────────────────────────────────────────────────┐
│                    🎯 欢迎使用A股分析系统                      │
│                                                             │
│  首次使用，让我们了解一下您的投资偏好                          │
│  这些信息将帮助我为您提供更个性化的分析建议                    │
└─────────────────────────────────────────────────────────────┘
        """)

        questions = [
            ("name", "📛 怎么称呼您？", "可直接回车使用默认名称"),
            ("risk_preference", "🎲 您的风险偏好？", "conservative(保守) / moderate(稳健) / aggressive(积极)", ["conservative", "moderate", "aggressive"]),
            ("investment_style", "📈 您的投资风格？", "value(价值) / growth(成长) / trend(趋势)", ["value", "growth", "trend"]),
            ("investment_horizon", "⏱️ 您的投资周期？", "short(短线) / medium(中线) / long(长线)", ["short", "medium", "long"]),
            ("focus_sectors", "👀 关注哪些行业？", "如: 科技,新能源,医药,金融（逗号分隔）"),
            ("avoid_sectors", "🚫 回避哪些行业/类型？", "如: ST股票,次新股（逗号分隔）"),
            ("max_position", "💰 单票最大仓位？", "百分比，如: 20"),
            ("stop_loss", "⛔ 止损比例？", "百分比，如: 10"),
            ("take_profit", "✅ 止盈比例？", "百分比，如: 20"),
        ]

        answers = {}

        for field, question, hint, *options in questions:
            while True:
                print(f"\n{question}")
                print(f"   💡 {hint}")

                if options:
                    print(f"   可选: {', '.join(options[0])}")

                try:
                    answer = input("   您的回答: ").strip()

                    # 使用默认值
                    if not answer:
                        print("   ℹ️ 使用默认值")
                        continue

                    # 验证选项
                    if options and answer.lower() not in options[0]:
                        print(f"   ⚠️ 请输入有效选项: {', '.join(options[0])}")
                        continue

                    answers[field] = answer
                    break

                except KeyboardInterrupt:
                    print("\n\n跳过初始化，使用默认设置...")
                    break

        # 保存设置
        print("\n📋 正在保存您的设置...")

        for field, value in answers.items():
            if field in ["name", "risk_preference", "investment_style", "investment_horizon"]:
                setattr(user_memory.profile, field, value)
            elif field in ["focus_sectors", "avoid_sectors"]:
                sectors = [s.strip() for s in value.split(",") if s.strip()]
                setattr(user_memory.preferences, field, sectors)
            elif field in ["max_position", "stop_loss", "take_profit"]:
                # 转换为小数
                num_value = float(value.replace("%", "")) / 100
                setattr(user_memory.preferences, field, num_value)

        user_memory.save()

        # 记录系统事件：首次初始化
        user_memory.append_history(
            f"用户完成初始化: 名称={user_memory.profile.name}, "
            f"风险偏好={user_memory.profile.risk_preference}, "
            f"关注领域={', '.join(user_memory.preferences.focus_sectors)}",
            event_type="system"
        )

        print("""
┌─────────────────────────────────────────────────────────────┐
│                    ✅ 初始化完成！                             │
│                                                             │
│  您随时可以输入 /profile 查看或修改个人设置                    │
└─────────────────────────────────────────────────────────────┘
        """)

        # 显示当前设置
        print("\n📊 您的投资画像:")
        summary = user_memory.get_profile_summary()
        for key, value in summary.items():
            print(f"  • {key}: {value}")
        print()

    async def _start_full(self):
        """启动完整服务"""
        # 启动调度器（包含定时报告和监控）
        await self.scheduler.start()

        # 启动命令行交互（内部已启动Agent主循环）
        await self._start_cli()

    async def _start_cli(self):
        """启动命令行交互（使用Agent主循环模式）"""
        print("\n💬 进入交互模式，输入 'help' 查看帮助，输入 'exit' 退出\n")

        # 启动Agent主循环（支持WORK/IDLE两阶段）
        await self.agent.start()

        # 设置消息处理器（用于显示Agent回复）
        response_future = None

        def on_message(msg: str):
            """处理Agent回复"""
            nonlocal response_future
            print(f"\n🤖 AI: {msg}\n")
            if response_future and not response_future.done():
                response_future.set_result(msg)

        self.agent.set_message_handler(on_message)

        # 使用线程池运行阻塞的input
        loop = asyncio.get_event_loop()

        while self.running:
            try:
                # 等待一小段时间确保日志输出完成
                await asyncio.sleep(0.1)

                # 在线程池中运行input，避免阻塞事件循环
                user_input = await loop.run_in_executor(None, lambda: input("\r👤 您: "))
                user_input = user_input.strip()

                if not user_input:
                    continue

                if user_input.lower() in ["exit", "quit", "退出"]:
                    print("👋 再见！")
                    await self.agent.stop()
                    break

                if user_input.lower() in ["help", "帮助"]:
                    self._print_help()
                    continue

                # 处理特殊命令
                if user_input.startswith("/"):
                    await self._handle_command(user_input)
                    continue

                # 通过事件队列发送消息给Agent（主循环模式）
                print("🤖 AI正在分析...")
                response_future = asyncio.get_event_loop().create_future()
                await self.agent.post_event("user_message", user_input)

                # 等待回复（最多60秒）
                try:
                    await asyncio.wait_for(response_future, timeout=120.0)
                except asyncio.TimeoutError:
                    print("\n⏱️ 分析中\n")

            except KeyboardInterrupt:
                print("\n👋 再见！")
                await self.agent.stop()
                break
            except Exception as e:
                logger.error(f"处理输入时出错: {e}")
                print(f"❌ 出错了: {e}")

    async def _handle_command(self, command: str):
        """处理特殊命令"""
        parts = command.split()
        cmd = parts[0].lower()

        if cmd == "/watchlist":
            user_memory.append_history("用户查看自选列表", event_type="command")
            await self._show_watchlist()
        elif cmd == "/add":
            if len(parts) > 1:
                codes = parts[1:]
                user_memory.append_history(f"用户添加自选股: {', '.join(codes)}", event_type="command")
                from tools.all_tools import AddToWatchlistTool
                tool = AddToWatchlistTool()
                result = await tool.run(codes=codes)
                print(f"\n{result}\n")
            else:
                print("\n用法: /add 600036 000001\n")
        elif cmd == "/remove":
            if len(parts) > 1:
                codes = parts[1:]
                user_memory.append_history(f"用户移除自选股: {', '.join(codes)}", event_type="command")
                from tools.all_tools import RemoveFromWatchlistTool
                tool = RemoveFromWatchlistTool()
                result = await tool.run(codes=codes)
                print(f"\n{result}\n")
            else:
                print("\n用法: /remove 600036\n")
        elif cmd == "/report":
            user_memory.append_history("用户生成报告", event_type="command")
            print("\n📰 正在生成报告...")
            report = await morning_report_service.generate_report()
            print(f"\n{report[:500]}...\n")
        elif cmd == "/monitor":
            user_memory.append_history("用户启动监控服务", event_type="system")
            await monitoring_service.start()
            print("\n📡 已启动监控服务\n")
        elif cmd == "/stop":
            user_memory.append_history("用户停止监控服务", event_type="system")
            monitoring_service.stop()
            print("\n⏹️ 已停止监控服务\n")
        elif cmd == "/clear":
            user_memory.append_history("用户清除对话历史", event_type="system")
            self.agent.clear_history()
            print("\n🗑️ 对话历史已清除\n")
        elif cmd == "/profile":
            await self._show_profile()
        elif cmd == "/update":
            if len(parts) >= 3:
                field = parts[1]
                value = " ".join(parts[2:])
                from tools.all_tools import UpdateUserProfileTool
                tool = UpdateUserProfileTool()
                result = await tool.run(field=field, value=value, reason="用户手动更新")
                print(f"\n{result}\n")
            else:
                print("\n用法: /update <字段> <值>")
                print("示例: /update risk_preference conservative")
                print("      /update focus_sectors 科技,新能源,医药\n")
        elif cmd == "/session":
            await self._show_session_info()
        else:
            print(f"\n未知命令: {cmd}\n")

    async def _show_session_info(self):
        """显示会话信息"""
        info = self.agent.get_session_info()
        print("\n" + "=" * 50)
        print("📁 会话信息")
        print("=" * 50)
        print(f"会话ID: {info['session_id']}")
        print(f"总消息数: {info['total_messages']}")
        print(f"已压缩位置: {info['last_consolidated']}")
        print(f"有效历史: {info['active_messages']}")
        print(f"压缩次数: {info['compress_count']}")
        print(f"创建时间: {info['created_at']}")
        print(f"更新时间: {info['updated_at']}")
        print("=" * 50)
        print("💡 使用 --session <id> 启动不同会话")
        print("=" * 50 + "\n")

    async def _show_profile(self):
        """显示/修改用户画像"""
        print("\n" + "=" * 50)
        print("📊 用户画像")
        print("=" * 50)

        summary = user_memory.get_profile_summary()
        fields = [
            ("name", "姓名"),
            ("risk_preference", "风险偏好"),
            ("investment_style", "投资风格"),
            ("investment_horizon", "投资周期"),
            ("focus_sectors", "关注领域"),
            ("avoid_sectors", "回避领域"),
            ("max_position", "单票最大仓位"),
            ("stop_loss", "止损线"),
            ("take_profit", "止盈线"),
        ]

        for i, (key, label) in enumerate(fields, 1):
            value = summary.get(key, "未设置")
            if isinstance(value, list):
                value = ", ".join(value) if value else "无"
            print(f"{i}. {label}: {value}")

        print("-" * 50)
        print("💡 提示: 使用 /update <字段> <值> 修改")
        print("   例如: /update risk_preference conservative")
        print("=" * 50 + "\n")

    async def _show_watchlist(self):
        """显示自选列表"""
        from tools.all_tools import GetWatchlistTool
        tool = GetWatchlistTool()
        result = await tool.run()

        if result.success and result.data:
            print("\n⭐ 自选列表:")
            for item in result.data:
                code = item.get("code")
                notes = item.get("notes", "")
                print(f"  • {code} {f'({notes})' if notes else ''}")
            print()
        else:
            print("\n自选列表为空\n")

    def _print_help(self):
        """打印帮助信息"""
        help_text = """
┌─────────────────────────────────────────────────────────────┐
│                         帮助信息                             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  📊 基础功能:                                                │
│  • 直接输入股票代码分析，如: "分析600036"                     │
│  • 查询行情，如: "查看招商银行"                              │
│  • 获取新闻，如: "000001有什么新闻"                         │
│                                                             │
│  ⌨️  命令列表:                                               │
│  /watchlist    - 显示自选列表                               │
│  /add <代码>   - 添加股票到自选，如: /add 600036            │
│  /remove <代码> - 从自选移除，如: /remove 600036            │
│  /report       - 生成早间报告                               │
│  /monitor      - 启动实时监控                               │
│  /stop         - 停止监控                                   │
│  /clear        - 清除对话历史                               │
│  /profile      - 查看用户画像                               │
│  /update       - 更新用户画像，如: /update risk_preference conservative │
│  /session      - 查看会话信息                               │
│  exit          - 退出程序                                   │
│                                                             │
│  💡 提示:                                                    │
│  • 股票代码支持 600036 / sh600036 / 600036.SH 等格式        │
│  • 系统会自动保存自选到文件                                 │
│  • 设置 OPENAI_API_KEY 可获得完整AI分析功能                 │
│  • Agent会根据对话自动学习您的偏好（每周总结时更新）         │
│  • 对话历史会自动保存，重启后可继续                          │
│  • 使用 --session <id> 可创建多个独立对话                    │
│  • 关键事件（命令、画像更新等）记录在 memory/history.md      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
"""
        print(help_text)

    async def _generate_report(self):
        """生成报告"""
        report = await morning_report_service.generate_report()
        print(report)

    async def _start_monitor(self):
        """启动监控"""
        await monitoring_service.start()

        # 保持运行
        while self.running:
            await asyncio.sleep(1)

    async def _start_channel_servers(self):
        """启动外部渠道服务器（企业微信、飞书等）"""
        # 启动企业微信Webhook服务器
        if wecom_channel.enabled:
            started = await wecom_channel.start_webhook_server()
            if started:
                def on_wecom_message(msg):
                    logger.info(f"📱 企业微信消息已转交Agent处理: {msg.from_user}")
                wecom_channel.set_message_handler(on_wecom_message)
        else:
            logger.info("企业微信渠道未启用（缺少WECOM_CORP_ID等配置）")

        # 启动飞书WebSocket连接
        if feishu_channel.enabled:
            started = await feishu_channel.start()
            if started:
                def on_feishu_message(msg):
                    logger.info(f"📱 飞书消息已转交Agent处理: {msg.from_user}")
                feishu_channel.set_message_handler(on_feishu_message)
        else:
            logger.info("飞书渠道未启用（缺少FEISHU_APP_ID等配置）")

    async def stop(self):
        """停止系统"""
        self.running = False
        await self.scheduler.stop()
        monitoring_service.stop()
        await self.agent.stop()  # 停止Agent主循环
        await wecom_channel.stop_webhook_server()  # 停止企业微信Webhook
        await feishu_channel.stop()  # 停止飞书
        logger.info("🛑 系统已停止")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="A股分析Agent系统")
    parser.add_argument(
        "--mode", "-m",
        choices=["full", "cli", "report", "monitor"],
        default="full",
        help="启动模式 (默认: full)"
    )
    parser.add_argument(
        "--session", "-s",
        default="default",
        help="会话ID，用于区分不同对话历史 (默认: default)"
    )
    parser.add_argument(
        "--cli",
        action="store_const",
        const="cli",
        dest="mode",
        help="仅启动命令行交互"
    )
    parser.add_argument(
        "--report",
        action="store_const",
        const="report",
        dest="mode",
        help="仅生成一次报告"
    )
    parser.add_argument(
        "--monitor",
        action="store_const",
        const="monitor",
        dest="mode",
        help="仅启动监控服务"
    )

    args = parser.parse_args()

    system = AStockSystem(session_id=args.session)

    try:
        asyncio.run(system.start(args.mode))
    except KeyboardInterrupt:
        print("\n\n正在关闭系统...")
        asyncio.run(system.stop())
    except Exception as e:
        logger.error(f"系统错误: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
