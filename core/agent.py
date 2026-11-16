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
from model_requests import request_completion
from scheduled_tasks import ScheduledTaskService, TZ
from tools.registry import ToolRegistry, registry as tool_registry
from tools.all_tools import ALL_TOOLS
from core.memory import user_memory
from core.session import SessionManager, Session
from tools.all_tools import subagent_manager, skill_loader, context_compressor
from channels import wecom_channel, feishu_channel
from core.turn_runtime import run_event_loop

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
            self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=60, max_retries=0)

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
        self.scheduled_tasks = ScheduledTaskService(config.DATA_DIR / 'scheduled_tasks.json')

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

        # Pending files are consumed by the running loop, never deleted on startup.
        self._event_handler = None
        self._active_task = None
        self._pending_web = None

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

## 用户定时任务
用户要求稍后、每天或工作日执行时，调用 create_scheduled_task，不能仅口头承诺。
支持每日简报 daily_brief 或自定义研究 chat；时间均为北京时间。相对时间用 delay_minutes。
可通过 list_scheduled_tasks 查询，用 update_scheduled_task 暂停/启用，delete_scheduled_task 删除。
创建成功后说明任务名称、频率和下一次执行时间。定时触发时只执行研究，不再创建新任务。

当前时间（Asia/Shanghai）: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}
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
            raise

    def set_event_handler(self, handler):
        """Subscribe to public task events; channel delivery remains task-scoped."""
        self._event_handler = handler

    async def run_loop(self):
        await run_event_loop(self)

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
                    if event_data.get('event_type') == 'scheduled_task' and event_data.get('metadata', {}).get('session_id') != self.session.session_id:
                        continue
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

                    events.append(event)

                    # 标记为已处理并删除文件
                    self._processed_events.add(event_id)
                    event_file.unlink()
                    self.logger.debug(f"📥 从Mailbox读取事件: {event_id}")
                    break

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
        check_interval = min(0.25, self.idle_check_interval)
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
        return await request_completion(self.client,
            model=self.model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=self.temperature,
            max_tokens=2000,
            **config.model_request_options(),
        )

    async def start(self):
        """启动Agent主循环"""
        if self._main_loop_task is None or self._main_loop_task.done():
            self._shutdown_event.clear()
            await self.scheduled_tasks.start(self)
            self._main_loop_task = asyncio.create_task(self.run_loop())
            self.logger.info("✅ Agent主循环已启动")

    async def stop(self):
        """停止Agent主循环"""
        await self.scheduled_tasks.stop()
        self._shutdown_event.set()
        await self.post_event("shutdown_request", "Shutdown requested")
        if self._main_loop_task:
            self._main_loop_task.cancel()
            try:
                await self._main_loop_task
            except asyncio.CancelledError:
                pass
        if isinstance(self.client, AsyncOpenAI):
            await self.client.close()
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
                {"role": "system", "content": self._build_system_prompt()},
                *self._prepare_history_messages()
            ]

            tools = self._get_available_tools(use_tools)
            iteration = 0
            final_response = None

            while iteration < self.max_iterations:
                iteration += 1
                self.logger.info(f"🔄 主Agent第 {iteration}/{self.max_iterations} 轮")

                if await context_compressor.compact_session(self.session):
                    if not self.save_session():
                        raise RuntimeError("无法保存上下文摘要")
                messages = [
                    {"role": "system", "content": self._build_system_prompt()},
                    *self._prepare_history_messages(),
                ]

                response = await request_completion(self.client,
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto" if use_tools else None,
                    temperature=self.temperature,
                    max_tokens=2000,
                    **config.model_request_options(),
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
                    final_response = self._last_subagent_content or self._last_tool_error or "子Agent未生成报告。"
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
        # Keep tool-call/result pairs intact; the existing compressor bounds context.
        return self.session.get_history()

    def _get_available_tools(self, use_tools: bool) -> Optional[List[Dict]]:
        """获取可用工具列表"""
        if not use_tools:
            return None

        all_schemas = tool_registry.get_schemas()
        allowed_tools = set(config.MAIN_AGENT_TOOLS) if config.MAIN_AGENT_TOOLS else None

        if allowed_tools:
            return [s for s in all_schemas if s.get("function", {}).get("name") in allowed_tools]
        return all_schemas

    async def _execute_tool_calls_and_record(self, message, messages):
        """Execute and record tools once; the turn owns report delivery."""
        kwargs = {"tool_calls": [tc.model_dump() for tc in message.tool_calls]}
        reasoning = getattr(message, "reasoning_content", None)
        if reasoning:
            kwargs["reasoning_content"] = reasoning
        self.session.add_message("assistant", message.content or "", **kwargs)
        messages.append({"role": "assistant", "content": message.content or "", **kwargs})
        spawned = False
        self._last_subagent_content = None
        self._last_tool_error = None
        for tc in message.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
                if not isinstance(args, dict):
                    raise ValueError("工具参数必须为对象")
                result = await tool_registry.execute(name, **args)
                data = result.to_dict()
            except Exception:
                self.logger.exception("Tool %s failed", name)
                data = {"success": False, "error": "工具执行失败，请重试"}
            content = json.dumps(data, ensure_ascii=False, default=str)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": content})
            self.session.add_message("tool", content, tool_call_id=tc.id, name=name)
            if not data.get("success"):
                self._last_tool_error = "工具未能完成请求，请稍后重试"
            if name == "spawn_subagent":
                spawned = True
                if data.get("success"):
                    payload = data.get("data") or {}
                    self._last_subagent_content = payload.get("content")
                    result_id = payload.get("result_id")
                    if result_id:
                        self._processed_subagent_results.add(result_id)
                        (self.subagent_results_dir / (result_id + ".json")).unlink(missing_ok=True)
        return messages, self._last_tool_error is None, spawned

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


