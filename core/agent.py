"""
A股分析Agent核心
整合子智能体、Skill加载器和对话压缩功能
"""
from __future__ import annotations
from pathlib import Path
import json
import logging
import asyncio
from typing import Any, Dict, List, Optional, Callable
from datetime import datetime
from dataclasses import dataclass, field

from openai import AsyncOpenAI

from config import config
from tools.registry import ToolRegistry, registry as tool_registry
from tools.all_tools import ALL_TOOLS
from core.memory import user_memory
from core.session import SessionManager, Session
from tools.all_tools import subagent_manager, skill_loader, context_compressor
from channels import wecom_channel, feishu_channel

logger = logging.getLogger(__name__)


@dataclass
class AgentEvent:
    """Agent事件 - 用于触发从idle状态恢复"""
    event_type: str  # "user_message", "price_alert", "routine_analysis", "morning_report", "system"
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class AStockAgent:
    """
    A股分析Agent
    核心功能：
    1. 对话交互（支持多轮工具调用）
    2. 工具调用（包括股票工具、分析工具、系统工具）
    3. 股票分析
    4. 报告生成（使用子智能体）
    5. 上下文压缩
    """

    def __init__(self, session_id: str = "default"):
        self.logger = logging.getLogger(self.__class__.__name__)

        # 初始化OpenAI客户端
        api_key = config.OPENAI_API_KEY
        base_url = config.OPENAI_API_BASE or None

        if not api_key:
            self.logger.warning("⚠️ 未设置OPENAI_API_KEY，将使用模拟模式")
            self.client = None
        else:
            self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

        self.model = config.OPENAI_MODEL
        self.temperature = config.OPENAI_TEMPERATURE

        # 注册工具
        self._register_tools()

        # 设置子智能体管理器的 skill loader
        subagent_manager.set_skill_loader(skill_loader)

        # 系统提示词
        self.system_prompt = self._build_system_prompt()

        # 会话管理
        self.session_manager = SessionManager()
        self.session = self.session_manager.get_or_create(session_id)

        # 从会话加载对话历史
        self.conversation_history: List[Dict[str, Any]] = self.session.messages

        self.logger.info(f"📝 加载会话 {session_id}: "
                        f"总消息 {len(self.conversation_history)}, "
                        f"已压缩位置 {self.session.last_consolidated}, "
                        f"有效历史 {len(self.session.get_history())}")

        # Token 阈值
        self.token_threshold = 6000

        # Agent循环配置
        self.max_iterations = 100

        # ========== Idle/挂机状态配置 ==========
        self.status: str = "idle"  # "idle", "working", "shutdown"
        # 文件系统 Mailbox 替代 asyncio.Queue
        self.mailbox_dir: Path = config.MEMORY_DIR / "mailbox"
        self.mailbox_dir.mkdir(parents=True, exist_ok=True)
        self._processed_events: set = set()  # 已处理的事件ID集合

        # 子Agent结果Mailbox
        self.subagent_results_dir: Path = config.MEMORY_DIR / "subagent_results"
        self.subagent_results_dir.mkdir(parents=True, exist_ok=True)
        self._processed_subagent_results: set = set()  # 已处理的子Agent结果ID集合
        self.idle_check_interval: int = config.AGENT_IDLE_CHECK_INTERVAL  # idle状态检查间隔（秒）
        self.idle_max_duration: int = config.AGENT_IDLE_MAX_DURATION  # idle最大持续时间（秒，0=无限）
        self.max_work_iterations: int = config.AGENT_MAX_WORK_ITERATIONS  # WORK PHASE最大迭代次数
        self._main_loop_task: Optional[asyncio.Task] = None
        self._shutdown_event: asyncio.Event = asyncio.Event()
        self._message_handler: Optional[Callable[[str], None]] = None
        self._idle_since: Optional[datetime] = None  # 进入idle状态的时间
        self._idle_duration: int = 0  # 累计idle时间（秒）

        # 企业微信相关
        self._current_wecom_user: Optional[str] = None  # 当前企业微信用户ID

        # 飞书相关
        self._current_feishu_chat_id: Optional[str] = None  # 当前飞书会话ID
        self._current_feishu_chat_type: Optional[str] = None  # 当前飞书会话类型

        # 启动时清空旧Mailbox，避免处理历史事件
        self._clear_mailbox()

    def _register_tools(self):
        """注册所有工具"""
        self.logger.info(f"✅ 已注册 {len(tool_registry.list_tools())} 个工具")
        for tool_name in tool_registry.list_tools():
            self.logger.debug(f"  - {tool_name}")

    def _build_system_prompt(self) -> str:
        """构建系统提示词"""
        skill_descriptions = skill_loader.get_descriptions(config.MAIN_AGENT_SKILLS)

        return f"""你是A股分析助手，一个专业的股票投资分析AI。

## 你的能力
1. 实时行情查询 - 获取股票价格、涨跌幅、成交量等
2. 技术分析 - 计算MA、RSI、MACD、BOLL、KDJ等指标
3. 异常监控 - 检测价格异动、成交量异常、技术指标信号
4. 股票筛选 - 根据条件筛选股票
5. 新闻分析 - 获取并分析股票相关新闻
6. 自选股管理 - 维护用户的自选股票列表
7. 调研报告 - 使用子智能体生成深度分析报告
8. 上下文压缩 - 管理长对话历史

## 多轮工具调用
你支持**多轮工具调用**，可以：
- 先获取数据，分析后决定是否需要调用其他工具
- 根据中间结果动态调整分析策略

示例流程：
1. 获取行情 → 发现异常 → 获取新闻分析原因 → 给出建议
2. 筛选股票 → 对符合条件的股票逐个分析 → 综合推荐

## 可用技能
{skill_descriptions}

## 子智能体使用规则
当调用 `spawn_subagent` 生成深度研报时：
1. 子Agent会生成完整的分析报告
2. **子Agent完成后，工具返回结果中包含 `content` 字段，这是子Agent生成的报告内容**
3. **你必须将 `content` 字段的内容完整展示给用户，然后再调用 `idle` 工具**
4. 信任子Agent的分析结果，无需额外验证
5. 展示完成后，调用 `idle` 进入休息状态

## 自动触发的事件处理

系统会通过文件系统Mailbox自动唤醒你：

### 1. 股价异常告警 (price_alert)
**触发条件**: 监控系统检测到自选股出现价格异动、成交量异常或技术指标突破
**处理流程**:
- 事件内容包含异常股票的代码、名称、异常类型和当前数据
- 立即使用 `spawn_subagent` 创建子Agent
- 指定 `skill_name="alert_analysis"` 使用告警分析技能
- 子Agent完成后展示分析结果并主动提醒用户
- 完成后调用 `idle` 进入休息

### 2. 定期分析任务 (routine_analysis)
**触发条件**: 每30分钟无异常时自动触发
**处理流程**:
- 事件内容包含自选股概况数据
- 使用 `spawn_subagent` 创建子Agent进行定期分析
- 指定 `skill_name="research_report"` 使用研究报告技能
- 分析各股票走势变化、市场环境、关注点和操作建议
- 如果发现有值得关注的股票，给出具体建议
- 完成后调用 `idle` 进入休息

### 3. 早间报告任务 (morning_report)
**触发条件**: 每天上午9:00（交易日）自动触发
**处理流程**:
- 使用 `spawn_subagent` 创建子Agent生成早报
- 指定 `skill_name="morning_report"` 使用早间报告技能
- 生成内容包括：市场情绪、自选股分析、热门股票调研、板块资金流向、投资建议
- 完成后调用 `idle` 进入休息

## Idle/挂机机制
当满足以下条件时，调用 `idle` 工具进入挂机状态：
1. 用户查询已处理完毕，无需进一步分析
2. **子智能体报告已展示完毕**
3. 没有检测到股价异常变动
4. 等待用户新的输入或市场变化

Agent会自动在WORK和IDLE两阶段之间切换：
- WORK PHASE: 处理用户对话和工具调用
- IDLE PHASE: 无工作时进入挂机，定期轮询Mailbox检查新事件

## 工作原则
1. 基于数据说话，不主观臆断
2. 风险提示优先，不保证收益
3. 分析全面客观，多角度评估
4. 尊重用户偏好，个性化建议
5. 任务完成后主动进入idle状态节省资源

{user_memory.get_context()}

## 重要功能说明

## 回复格式
1. 分析类回复：先给出结论，再提供详细分析
2. 数据类回复：清晰展示数据，必要时用表格
3. 建议类回复：明确标注风险提示
4. 发现异常时：立即提醒，说明严重程度

## 注意事项
1. 股市有风险，投资需谨慎
2. 本分析仅供参考，不构成投资建议
3. 实时监控发现异常时会主动提醒用户

当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

    # ========== Idle/挂机状态管理 ==========

    def set_message_handler(self, handler: Callable[[str], None]):
        """设置消息处理器（用于CLI输出）"""
        self._message_handler = handler

    def _clear_mailbox(self):
        """清空Mailbox目录，删除所有旧的事件文件"""
        try:
            # 清空主Mailbox
            for event_file in self.mailbox_dir.glob("*.json"):
                try:
                    event_file.unlink()
                    logger.debug(f"清理旧事件文件: {event_file.name}")
                except Exception as e:
                    logger.warning(f"清理事件文件失败 {event_file}: {e}")

            # 清空子Agent结果Mailbox
            for result_file in self.subagent_results_dir.glob("*.json"):
                try:
                    result_file.unlink()
                    logger.debug(f"清理旧子Agent结果文件: {result_file.name}")
                except Exception as e:
                    logger.warning(f"清理结果文件失败 {result_file}: {e}")

            logger.info("📭 Mailbox已清空")
        except Exception as e:
            logger.error(f"清空Mailbox失败: {e}")

    def _set_status(self, status: str):
        """设置Agent状态"""
        old_status = self.status
        self.status = status

        if old_status != status:
            # 状态图标
            status_icons = {
                "idle": "💤",
                "working": "▶️",
                "shutdown": "🛑"
            }
            icon = status_icons.get(status, "🔄")
            self.logger.info(f"{icon} Agent状态切换: {old_status} → {status}")

            # 记录进入idle的时间
            if status == "idle":
                self._idle_since = datetime.now()
            elif old_status == "idle":
                # 从idle恢复，计算持续时间
                if self._idle_since:
                    duration = (datetime.now() - self._idle_since).total_seconds()
                    self._idle_duration += int(duration)
                    self._idle_since = None

    async def post_event(self, event_type: str, content: str, metadata: Dict[str, Any] = None):
        """
        发送事件到文件系统 Mailbox
        用于从外部触发Agent从idle状态恢复
        """
        event = AgentEvent(
            event_type=event_type,
            content=content,
            metadata=metadata or {}
        )

        # 写入文件系统
        event_id = f"{event_type}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        event_file = self.mailbox_dir / f"{event_id}.json"

        try:
            event_data = {
                "id": event_id,
                "event_type": event.event_type,
                "content": event.content,
                "metadata": event.metadata,
                "timestamp": event.timestamp
            }
            event_file.write_text(json.dumps(event_data, ensure_ascii=False, indent=2), encoding='utf-8')
            self.logger.debug(f"📨 事件写入Mailbox: {event_id}")
        except Exception as e:
            self.logger.error(f"写入Mailbox失败: {e}")

    async def run_loop(self):
        """
        主Agent循环 - 支持WORK/IDLE两阶段

        WORK PHASE: 处理用户对话和工具调用（最多max_work_iterations轮）
        IDLE PHASE: 无工作时进入挂机状态，定期检查事件队列
        """
        max_duration_str = f"{self.idle_max_duration}s" if self.idle_max_duration > 0 else "无限"
        self.logger.info("=" * 50)
        self.logger.info("🚀 启动Agent主循环（WORK/IDLE两阶段）")
        self.logger.info(f"   检查间隔: {self.idle_check_interval}s | 最大IDLE: {max_duration_str} | 最大WORK迭代: {self.max_work_iterations}")
        self.logger.info("=" * 50)
        self._set_status("idle")

        # 启动时先进入 IDLE，等待第一个事件触发
        first_event = True

        while not self._shutdown_event.is_set():
            # ========== 启动时或WORK完成后，先进入IDLE等待事件 ==========
            if first_event:
                first_event = False
                # 启动时直接进入IDLE，不处理历史消息
                idle_start = datetime.now()
                max_duration_str = f"{self.idle_max_duration}s" if self.idle_max_duration > 0 else "无限"

                # 添加入idle状态的助手消息
                idle_message = "有新的消息或股价异常时请随时唤醒我。💤"
                self.session.add_message("assistant", idle_message)
                if self._message_handler:
                    self._message_handler(idle_message)

                # 设置 idle 状态时间
                self._idle_since = datetime.now()

                self.logger.info(f"💤 进入IDLE状态 | 检查间隔: {self.idle_check_interval}s | 最大持续时间: {max_duration_str}")

                # 等待第一个事件
                while not self._shutdown_event.is_set():
                    has_event = await self._wait_for_event_or_timeout()
                    if has_event:
                        idle_duration = (datetime.now() - idle_start).total_seconds()
                        self.logger.info(f"📥 从IDLE恢复 | 本次IDLE时长: {idle_duration:.1f}s")
                        break
                    if self.idle_max_duration > 0 and self._idle_since:
                        current_idle = (datetime.now() - self._idle_since).total_seconds()
                        if current_idle >= self.idle_max_duration:
                            self.logger.info(f"⏰ IDLE达到最大持续时间({self.idle_max_duration}s)，触发定期任务")
                            idle_duration = (datetime.now() - idle_start).total_seconds()
                            self.logger.info(f"📥 从IDLE恢复 | 本次IDLE时长: {idle_duration:.1f}s")
                            break

            # ========== WORK PHASE ==========
            # 每次进入WORK PHASE时重置messages，从干净的系统提示开始
            messages: List[Dict[str, Any]] = [
                {"role": "system", "content": self.system_prompt}
            ]

            self._set_status("working")
            work_start = datetime.now()
            work_rounds = 0
            has_work = True

            self.logger.info(f"▶️ 进入WORK状态 | 最大迭代: {self.max_work_iterations}轮")

            while has_work and work_rounds < self.max_work_iterations:
                work_rounds += 1

                # 1. 检查Mailbox中的新消息（系统事件和子Agent结果）
                inbox_events = self._drain_mailbox()
                subagent_results = self._drain_subagent_results()

                # 如果是WORK PHASE的第一轮且没有新事件，返回IDLE
                # （避免在没有实际工作时调用LLM）
                if work_rounds == 1 and not inbox_events and not subagent_results:
                    self.logger.debug("WORK PHASE启动但没有新事件，返回IDLE")
                    has_work = False
                    break

                # 处理子Agent结果：直接展示给用户，无需主Agent调用LLM总结
                if subagent_results:
                    for result in subagent_results:
                        content = result.get('content', '')
                        task_name = result.get('task_name', 'unknown')

                        # 直接展示子Agent结果给用户
                        display_msg = f"\n📊 **{task_name}**\n\n{content}\n"
                        if self._message_handler:
                            self._message_handler(display_msg)

                        # 记录到会话历史
                        self.session.add_message("assistant", display_msg)

                        # 发送到企业微信/飞书
                        if self._current_wecom_user:
                            await wecom_channel.send_message(
                                self._current_wecom_user,
                                f"**{task_name}**\n\n{content}",
                                msg_type="markdown"
                            )
                            self._current_wecom_user = None

                        if self._current_feishu_chat_id:
                            await feishu_channel.send_message(
                                self._current_feishu_chat_id,
                                f"**{task_name}**\n\n{content}",
                                msg_type="interactive"
                            )
                            self._current_feishu_chat_id = None
                            self._current_feishu_chat_type = None

                        self.logger.info(f"📋 子Agent结果直接展示: {task_name}")

                    # 子Agent结果已直接展示，无需再调用LLM，直接返回IDLE
                    has_work = False
                    break

                # 处理其他事件（用户消息、系统事件等）
                for event in inbox_events:
                    if event.event_type == "shutdown_request":
                        self._set_status("shutdown")
                        return
                    # 系统事件特殊处理：添加系统提示引导Agent使用子Agent
                    if event.event_type in ["price_alert", "routine_analysis", "morning_report"]:
                        # 添加系统提示，指导Agent如何处理
                        skill_map = {
                            "price_alert": "alert_analysis",
                            "routine_analysis": "research_report",
                            "morning_report": "morning_report"
                        }
                        skill_hint = skill_map.get(event.event_type, "research_report")
                        messages.append({
                            "role": "system",
                            "content": f"【系统事件: {event.event_type}】检测到需要处理的事件。请使用 spawn_subagent 工具，skill_name='{skill_hint}' 进行专业分析。"
                        })
                        messages.append({
                            "role": "user",
                            "content": f"[{event.event_type}] {event.content}"
                        })
                    else:
                        messages.append({
                            "role": "user",
                            "content": f"[{event.event_type}] {event.content}"
                        })

                # 2. 检查是否需要压缩上下文
                if context_compressor.should_compress(messages):
                    messages = await context_compressor.compress_if_needed(
                        messages, session=self.session
                    )

                # 3. 调用LLM
                try:
                    response = await self._call_llm(messages)
                except Exception as e:
                    self.logger.error(f"LLM调用失败: {e}")
                    self._set_status("idle")
                    break

                # 4. 检查是否需要调用工具
                message_obj = response.choices[0].message

                if response.choices[0].finish_reason != "tool_calls":
                    # 没有工具调用，本轮工作完成
                    # 添加助手消息到历史
                    reasoning = getattr(message_obj, 'reasoning_content', None)
                    assistant_msg = self.session.add_message(
                        "assistant",
                        message_obj.content or "",
                        **({"reasoning_content": reasoning} if reasoning else {})
                    )
                    if self._message_handler and message_obj.content:
                        self._message_handler(message_obj.content)

                    # 如果消息来自企业微信，发送回复到企业微信
                    if self._current_wecom_user and message_obj.content:
                        await wecom_channel.send_message(
                            self._current_wecom_user,
                            message_obj.content
                        )
                        # 清空当前用户，避免后续消息误发
                        self._current_wecom_user = None

                    # 如果消息来自飞书，发送回复到飞书
                    if self._current_feishu_chat_id and message_obj.content:
                        await feishu_channel.send_message(
                            self._current_feishu_chat_id,
                            message_obj.content
                        )
                        # 清空当前会话，避免后续消息误发
                        self._current_feishu_chat_id = None
                        self._current_feishu_chat_type = None

                    has_work = False
                    break

                # 5. 有工具调用，执行它们
                # _execute_tool_calls_and_record 会正确处理助手消息和工具消息
                messages, tool_executed, has_spawn_subagent = await self._execute_tool_calls_and_record(
                    message_obj, messages
                )

                # 如果调用了 spawn_subagent，结果已经直接展示给用户，无需再调用LLM
                if has_spawn_subagent:
                    self.logger.info("📋 spawn_subagent 已完成并展示结果，结束WORK PHASE")
                    has_work = False
                    break

                # 检查是否有idle工具调用
                for tc in message_obj.tool_calls:
                    if tc.function.name == "idle":
                        has_work = False
                        break

            # WORK PHASE结束，记录统计
            work_duration = (datetime.now() - work_start).total_seconds()
            self.logger.info(f"✅ WORK完成 | 迭代: {work_rounds}轮 | 耗时: {work_duration:.1f}s")

            # ========== IDLE PHASE ==========
            self._set_status("idle")
            idle_start = datetime.now()
            max_duration_str = f"{self.idle_max_duration}s" if self.idle_max_duration > 0 else "无限"

            # 添加入idle状态的助手消息
            idle_message = "我的任务完成，我将进入休息。有新的消息或股价异常时请随时唤醒我。💤"
            self.session.add_message("assistant", idle_message)
            if self._message_handler:
                self._message_handler(idle_message)

            self.logger.info(f"💤 进入IDLE状态 | 检查间隔: {self.idle_check_interval}s | 最大持续时间: {max_duration_str}")

            # 保存会话状态
            self.session_manager.save(self.session)

            # 短暂延迟，避免立即检测到残留事件
            await asyncio.sleep(0.5)

            # IDLE循环：持续等待事件，直到有新事件才恢复WORK
            while not self._shutdown_event.is_set():
                # 等待事件或超时检查
                has_event = await self._wait_for_event_or_timeout()

                if has_event:
                    # 有新事件，退出IDLE循环，进入WORK
                    idle_duration = (datetime.now() - idle_start).total_seconds()
                    self.logger.info(f"📥 从IDLE恢复 | 本次IDLE时长: {idle_duration:.1f}s")
                    break

                # 没有事件，继续IDLE（继续循环等待）
                # 检查是否需要触发定期任务（如果配置了最大idle时间）
                if self.idle_max_duration > 0 and self._idle_since:
                    current_idle = (datetime.now() - self._idle_since).total_seconds()
                    if current_idle >= self.idle_max_duration:
                        self.logger.info(f"⏰ IDLE达到最大持续时间({self.idle_max_duration}s)，触发定期任务")
                        idle_duration = (datetime.now() - idle_start).total_seconds()
                        self.logger.info(f"📥 从IDLE恢复 | 本次IDLE时长: {idle_duration:.1f}s")
                        break
                # 继续等待（保持在IDLE状态）
                continue

        self.logger.info("🛑 Agent主循环已停止")

    def _drain_mailbox(self) -> List[AgentEvent]:
        """
        从文件系统 Mailbox 读取所有待处理的事件
        读取后删除已处理的事件文件
        """
        events = []

        try:
            # 获取所有事件文件（按时间排序）
            event_files = sorted(self.mailbox_dir.glob("*.json"))

            for event_file in event_files:
                try:
                    event_data = json.loads(event_file.read_text(encoding='utf-8'))
                    # 优先从JSON读取id，如果没有则使用文件名（stem）
                    event_id = event_data.get("id") or event_file.stem

                    # 跳过已处理的事件
                    if event_id in self._processed_events:
                        logger.debug(f"跳过已处理的事件: {event_id}")
                        continue

                    # 忽略启动前的shutdown_request事件
                    if event_data["event_type"] == "shutdown_request":
                        logger.debug(f"忽略旧的shutdown_request事件: {event_id}")
                        event_file.unlink()
                        continue

                    event = AgentEvent(
                        event_type=event_data["event_type"],
                        content=event_data["content"],
                        metadata=event_data.get("metadata", {}),
                        timestamp=event_data.get("timestamp", datetime.now().isoformat())
                    )

                    # 如果是企业微信消息，记录用户ID
                    if event.event_type == "wecom_message":
                        from_user = event.metadata.get("from_user")
                        if from_user:
                            self._current_wecom_user = from_user
                            self.logger.info(f"📱 收到企业微信消息，用户: {from_user}")

                    # 如果是飞书消息，记录会话信息
                    if event.event_type == "feishu_message":
                        chat_id = event.metadata.get("chat_id")
                        chat_type = event.metadata.get("chat_type")
                        if chat_id:
                            self._current_feishu_chat_id = chat_id
                            self._current_feishu_chat_type = chat_type
                            self.logger.info(f"📱 收到飞书消息，会话: {chat_id}")

                    events.append(event)

                    # 标记为已处理并删除文件
                    self._processed_events.add(event_id)
                    event_file.unlink()
                    self.logger.debug(f"📥 从Mailbox读取事件: {event_id}")

                except Exception as e:
                    self.logger.error(f"读取事件文件失败 {event_file}: {e}")
                    continue

            # 清理已处理事件集合（防止无限增长）
            if len(self._processed_events) > 1000:
                self._processed_events = set(list(self._processed_events)[-500:])

        except Exception as e:
            self.logger.error(f"读取Mailbox失败: {e}")

        return events

    def _drain_subagent_results(self) -> List[Dict[str, Any]]:
        """
        从子Agent结果Mailbox读取所有已完成的结果
        读取后删除已处理的文件
        """
        results = []

        try:
            # 获取所有结果文件
            result_files = sorted(self.subagent_results_dir.glob("*.json"))

            for result_file in result_files:
                try:
                    result_data = json.loads(result_file.read_text(encoding='utf-8'))
                    result_id = result_data.get("id")

                    # 跳过已处理的结果
                    if result_id in self._processed_subagent_results:
                        continue

                    results.append({
                        "id": result_id,
                        "task_name": result_data.get("task_name", "unknown"),
                        "content": result_data.get("content", ""),
                        "metadata": result_data.get("metadata", {}),
                        "timestamp": result_data.get("timestamp", datetime.now().isoformat())
                    })

                    # 标记为已处理并删除文件
                    self._processed_subagent_results.add(result_id)
                    result_file.unlink()
                    self.logger.info(f"📥 读取子Agent结果: {result_id}")

                except Exception as e:
                    self.logger.error(f"读取子Agent结果文件失败 {result_file}: {e}")
                    continue

            # 清理已处理结果集合
            if len(self._processed_subagent_results) > 1000:
                self._processed_subagent_results = set(list(self._processed_subagent_results)[-500:])

        except Exception as e:
            self.logger.error(f"读取子Agent结果Mailbox失败: {e}")

        return results

    async def _wait_for_event_or_timeout(self) -> bool:
        """
        在idle状态轮询文件系统Mailbox或超时
        返回: True=有事件需要处理, False=继续idle
        """
        check_interval = 5  # 每5秒检查一次文件系统
        elapsed = 0

        while elapsed < self.idle_check_interval:
            # 检查是否有新事件文件
            try:
                event_files = list(self.mailbox_dir.glob("*.json"))
                if event_files:
                    # 过滤掉已处理的事件
                    new_events = []
                    for f in event_files:
                        # 优先从JSON读取id，如果没有则使用文件名
                        try:
                            event_data = json.loads(f.read_text(encoding='utf-8'))
                            event_id = event_data.get("id") or f.stem
                        except Exception:
                            event_id = f.stem
                        if event_id not in self._processed_events:
                            new_events.append(f)

                    if new_events:
                        self.logger.debug(f"📬 Mailbox检测到 {len(new_events)} 个新事件")
                        return True
            except Exception as e:
                self.logger.error(f"轮询Mailbox失败: {e}")

            # 检查是否有新的子Agent结果
            try:
                result_files = list(self.subagent_results_dir.glob("*.json"))
                if result_files:
                    # 过滤掉已处理的结果
                    new_results = []
                    for f in result_files:
                        result_id = f.stem
                        if result_id not in self._processed_subagent_results:
                            new_results.append(f)

                    if new_results:
                        self.logger.debug(f"📬 子Agent结果检测到 {len(new_results)} 个新结果")
                        return True
            except Exception as e:
                self.logger.error(f"轮询子Agent结果失败: {e}")

            # 检查是否超过最大idle时间
            if self.idle_max_duration > 0 and self._idle_since:
                current_idle = (datetime.now() - self._idle_since).total_seconds()
                if current_idle >= self.idle_max_duration:
                    self.logger.info(f"⏰ IDLE超时 | 持续时间: {current_idle:.0f}s | 阈值: {self.idle_max_duration}s")
                    return True

            # 等待后继续检查
            await asyncio.sleep(check_interval)
            elapsed += check_interval

        return False

    async def _call_llm(self, messages: List[Dict[str, Any]]) -> Any:
        """调用LLM"""
        tools = self._get_available_tools(True)
        return await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=self.temperature,
            max_tokens=2000
        )

    async def start(self):
        """启动Agent主循环"""
        if self._main_loop_task is None or self._main_loop_task.done():
            self._shutdown_event.clear()
            self._main_loop_task = asyncio.create_task(self.run_loop())
            self.logger.info("✅ Agent主循环已启动")

    async def stop(self):
        """停止Agent主循环"""
        self._shutdown_event.set()
        await self.post_event("shutdown_request", "Shutdown requested")
        if self._main_loop_task:
            self._main_loop_task.cancel()
            try:
                await self._main_loop_task
            except asyncio.CancelledError:
                pass
        self.logger.info("⏹️ Agent主循环已停止")

    # ========== 原有的chat方法（兼容模式）==========

    async def chat(self, message: str, use_tools: bool = True) -> str:
        """
        对话交互 - 兼容原有调用方式

        如果Agent主循环在运行，通过事件队列触发
        否则，直接同步处理（向后兼容）

        Args:
            message: 用户消息
            use_tools: 是否允许使用工具

        Returns:
            AI回复
        """
        # 如果主循环在运行，通过事件触发
        if self._main_loop_task and not self._main_loop_task.done():
            await self.post_event("user_message", message)
            # 等待处理完成（通过Future机制）
            return "[消息已发送到Agent事件队列]"

        # 否则，直接处理（向后兼容模式）
        return await self._sync_chat(message, use_tools)

    async def _sync_chat(self, message: str, use_tools: bool = True) -> str:
        """
        同步对话处理（向后兼容）
        当主循环未启动时，直接处理用户消息
        """
        # 添加用户消息到会话
        user_msg = self.session.add_message("user", message)
        self.conversation_history = self.session.messages

        # 检查是否需要压缩上下文
        if context_compressor.should_compress(self.conversation_history):
            self.logger.info("对话上下文超过阈值，执行压缩")
            self.conversation_history = await context_compressor.compress_if_needed(
                self.conversation_history,
                session=self.session
            )
            self.session.messages = self.conversation_history

        if self.client is None:
            response = await self._mock_chat(message)
            self.conversation_history.append({
                "role": "assistant",
                "content": response,
                "timestamp": datetime.now().isoformat()
            })
            return response

        try:
            messages = [
                {"role": "system", "content": self.system_prompt},
                *self._prepare_history_messages(),
                {"role": "user", "content": message}
            ]

            tools = self._get_available_tools(use_tools)
            iteration = 0
            final_response = None

            while iteration < self.max_iterations:
                iteration += 1
                self.logger.info(f"🔄 主Agent第 {iteration}/{self.max_iterations} 轮")

                messages = await context_compressor.compress_if_needed(
                    messages, session=self.session
                )

                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto" if use_tools else None,
                    temperature=self.temperature,
                    max_tokens=2000
                )

                message_obj = response.choices[0].message

                if response.choices[0].finish_reason != "tool_calls":
                    final_response = message_obj.content or "分析完成。"
                    self.logger.info(f"✅ 主Agent完成，共 {iteration} 轮")
                    break

                messages, tool_executed, has_spawn_subagent = await self._execute_tool_calls_and_record(
                    message_obj, messages
                )

                # 如果调用了 spawn_subagent，结果已经直接展示
                if has_spawn_subagent:
                    final_response = "子Agent报告已生成并展示。"
                    self.logger.info(f"✅ 主Agent完成（通过spawn_subagent），共 {iteration} 轮")
                    break

                if not tool_executed:
                    final_response = "工具调用执行失败，无法完成任务。"
                    break

            if final_response is None:
                final_response = f"已达到最大迭代次数 ({self.max_iterations})，任务未能完全完成。"

            self.session.add_message("assistant", final_response)
            self.conversation_history = self.session.messages
            self.session_manager.save(self.session)

            return final_response

        except Exception as e:
            self.logger.error(f"对话失败: {e}")
            error_msg = f"抱歉，调用AI服务失败: {e}"
            self.session.add_message("assistant", error_msg)
            self.conversation_history = self.session.messages
            self.session_manager.save(self.session)
            return error_msg

    def _prepare_history_messages(self) -> List[Dict[str, str]]:
        """准备历史消息（从会话获取，自动处理压缩位置）"""
        # 使用session的get_history方法，自动从last_consolidated开始
        return self.session.get_history(max_messages=20)

    def _get_available_tools(self, use_tools: bool) -> Optional[List[Dict]]:
        """获取可用工具列表"""
        if not use_tools:
            return None

        all_schemas = tool_registry.get_schemas()
        allowed_tools = set(config.MAIN_AGENT_TOOLS) if config.MAIN_AGENT_TOOLS else None

        if allowed_tools:
            return [s for s in all_schemas if s.get("function", {}).get("name") in allowed_tools]
        return all_schemas

    async def _execute_tool_calls_and_record(
        self,
        message: Any,
        messages: List[Dict]
    ) -> tuple[List[Dict], bool, bool]:
        """
        执行工具调用，并将结果记录到消息列表和历史中

        Args:
            message: LLM响应的消息对象
            messages: 当前消息列表

        Returns:
            (更新后的消息列表, 是否成功执行, 是否有spawn_subagent调用)
        """
        tool_calls = message.tool_calls
        has_spawn_subagent = False

        # 构建助手消息（包含工具调用请求）
        # 注意：必须保留所有字段，包括reasoning_content（Moonshot API要求）
        reasoning = getattr(message, 'reasoning_content', None)

        # 使用session添加消息（会自动添加timestamp）
        assistant_kwargs = {"tool_calls": [tc.model_dump() for tc in tool_calls]}
        if reasoning:
            assistant_kwargs["reasoning_content"] = reasoning

        assistant_msg_for_history = self.session.add_message(
            "assistant",
            message.content or "",
            **assistant_kwargs
        )

        # 构建给API的消息（清理timestamp等内部字段）
        assistant_msg_for_api = {
            "role": "assistant",
            "content": message.content or "",
            "tool_calls": [tc.model_dump() for tc in tool_calls],
        }
        if reasoning:
            assistant_msg_for_api["reasoning_content"] = reasoning

        # 添加到本地消息列表（给API用的）
        messages.append(assistant_msg_for_api)

        # 执行工具调用
        tool_results = []
        for tc in tool_calls:
            tool_name = tc.function.name
            if tool_name == "spawn_subagent":
                has_spawn_subagent = True

            try:
                tool_args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                tool_args = {}

            self.logger.info(f"🛠️ 调用工具: {tool_name}({tool_args})")

            try:
                result = await tool_registry.execute(tool_name, **tool_args)
                result_data = result.to_dict() if hasattr(result, 'to_dict') else {"success": True, "data": result}
                result_content = json.dumps(result_data, ensure_ascii=False)

                self.logger.info(f"✅ 工具 {tool_name} 执行成功")

                # 特殊处理 spawn_subagent：自动展示报告内容给用户
                if tool_name == "spawn_subagent" and result_data.get("success"):
                    content = result_data.get("data", {}).get("content", "")
                    task_name = result_data.get("data", {}).get("task_name", "子Agent任务")
                    result_id = result_data.get("data", {}).get("result_id")
                    if content:
                        if self._message_handler:
                            self.logger.info(f"📋 展示子Agent报告给用户")
                            self._message_handler(f"\n📊 **{task_name}**\n\n{content}\n")
                        # 如果消息来自企业微信，发送报告到企业微信
                        if self._current_wecom_user:
                            await wecom_channel.send_message(
                                self._current_wecom_user,
                                f"**{task_name}**\n\n{content}",
                                msg_type="markdown"
                            )
                        # 如果消息来自飞书，发送报告到飞书
                        if self._current_feishu_chat_id:
                            await feishu_channel.send_message(
                                self._current_feishu_chat_id,
                                f"**{task_name}**\n\n{content}",
                                msg_type="interactive"
                            )
                    # 将子Agent结果标记为已处理，避免后续从mailbox重复读取
                    if result_id:
                        self._processed_subagent_results.add(result_id)
                        self.logger.debug(f"已将子Agent结果标记为已处理: {result_id}")

            except Exception as e:
                self.logger.error(f"工具调用失败: {e}")
                result_content = json.dumps({"error": str(e)}, ensure_ascii=False)

            # 添加到本地消息列表（给API用）
            tool_msg_api = {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_content
            }
            messages.append(tool_msg_api)

            # 添加到会话（会自动添加timestamp）
            tool_msg_history = self.session.add_message(
                "tool",
                result_content,
                tool_call_id=tc.id,
                name=tool_name
            )
            tool_results.append(result_content)

        return messages, True, has_spawn_subagent

    async def _mock_chat(self, message: str) -> str:
        """模拟对话（无API key时使用）"""
        # 简单的关键词匹配
        message_lower = message.lower()

        if any(kw in message_lower for kw in ["行情", "价格", "股票", "股"]):
            return "（模拟模式）我目前无法获取实时行情数据。请设置 OPENAI_API_KEY 以使用完整功能。"

        if any(kw in message_lower for kw in ["帮助", "help", "能做什么"]):
            return """（模拟模式）我可以帮您：
1. 查询股票实时行情
2. 分析技术指标
3. 获取股票新闻
4. 管理自选股
5. 生成分析报告

请设置 OPENAI_API_KEY 以使用完整功能。"""

        return "（模拟模式）请设置 OPENAI_API_KEY 以使用完整功能。"

    def clear_history(self) -> None:
        """清空当前会话历史"""
        self.session.clear()
        self.conversation_history = self.session.messages
        self.session_manager.save(self.session)
        self.logger.info(f"🗑️ 已清空会话 {self.session.session_id} 的历史")

    def save_session(self) -> bool:
        """手动保存当前会话"""
        return self.session_manager.save(self.session)

    def get_session_info(self) -> Dict[str, Any]:
        """获取当前会话信息和Agent状态"""
        # 计算当前idle状态持续时间
        current_idle_duration = 0
        if self.status == "idle" and self._idle_since:
            current_idle_duration = int((datetime.now() - self._idle_since).total_seconds())

        # 累计idle时间 + 当前idle时间
        total_idle_duration = self._idle_duration + current_idle_duration

        return {
            # 会话信息
            "session_id": self.session.session_id,
            "total_messages": len(self.session.messages),
            "last_consolidated": self.session.last_consolidated,
            "active_messages": len(self.session.get_history()),
            "compress_count": self.session.compress_count,
            "created_at": self.session.created_at,
            "updated_at": self.session.updated_at,
            # Agent状态信息
            "agent_status": self.status,
            "main_loop_running": self._main_loop_task is not None and not self._main_loop_task.done(),
            "idle_since": self._idle_since.isoformat() if self._idle_since else None,
            "current_idle_duration_sec": current_idle_duration,
            "total_idle_duration_sec": total_idle_duration,
            # Idle配置
            "idle_check_interval_sec": self.idle_check_interval,
            "idle_max_duration_sec": self.idle_max_duration if self.idle_max_duration > 0 else "unlimited",
            "max_work_iterations": self.max_work_iterations,
        }


