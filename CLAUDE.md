# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is **AStack** (A股分析Agent系统) - An intelligent A-share (Chinese stock market) analysis system based on LLM and AKShare. It provides real-time monitoring, daily morning reports, and conversational stock analysis.

Key capabilities:
1. **Real-time monitoring + Agent Trigger** - Heartbeat-based monitoring (1min interval) triggers Agent analysis via message queue on anomalies
2. **Periodic Analysis** - Every 30 minutes, triggers Agent to analyze all watchlist stocks when no anomalies detected
3. **Agent WORK/IDLE Mode** - Dual-phase architecture: WORK phase for task processing, IDLE phase for resource saving, event-driven wake-up
4. **Morning reports** - Generates pre-market analysis reports daily (via SubAgent + Skill)
5. **Conversational analysis** - Natural language interaction for stock analysis with multi-round tool calling
6. **Research reports** - Deep analysis reports using SubAgent with research_report Skill
7. **Alert analysis** - Automatic anomaly analysis using SubAgent with alert_analysis Skill
8. **User memory system** - Two-layer memory: user profile + session history with compression tracking
9. **Context compression** - Smart conversation compression preserving stock-related info
10. **Session persistence** - Complete conversation history saved and resumed across restarts
11. **Multi-session support** - Use `--session <id>` for isolated conversation contexts
12. **WeCom (企业微信) Integration** - Bidirectional communication via webhook server + API
    - Users send messages from WeCom → Agent receives via mailbox
    - Agent replies → Automatically sent to WeCom users
    - Morning reports and alerts sent to configured users
13. **Feishu (飞书) Integration** - Bidirectional communication via WebSocket
    - WebSocket long connection, no public IP required
    - Interactive card messages with markdown support
    - Message reactions (👍) for acknowledgment

## Project Structure

```
astack/                          # Main project directory
├── main.py                      # Entry point - start services and CLI
├── config.py                    # Configuration via Config dataclass
├── requirements.txt             # Python dependencies
├── .env.example                 # Environment variable template
├── user.md                      # User long-term memory (auto-maintained)
│
├── core/                        # Core modules
│   ├── agent.py                 # AStockAgent - main agent with tool calling + context compression + multi-round loop
│   ├── memory.py                # UserMemory - persists user profile to user.md and history.md
│   ├── session.py               # Session & SessionManager - persistent conversation history
│   ├── scheduler.py             # TaskScheduler - manages monitoring/report timing
│   ├── notifier.py              # NotificationManager - console/dingtalk/wecom
│   ├── subagent.py              # SubAgent - handles complex tasks (reports)
│   ├── skill_loader.py          # SkillLoader - loads SKILL.md definitions
│   └── context_compressor.py    # ContextCompressor - smart conversation compression
│
│
├── data/                        # Data layer
│   ├── akshare_client_fixed.py  # Main AKShare client (patched with retry/rate-limiting)
│   └── indicators.py            # TechnicalIndicators (RSI, MACD, BOLL, KDJ, etc.)
│
├── tools/                       # Tool layer for agent
│   ├── base.py                  # Tool base class
│   ├── registry.py              # ToolRegistry - global tool registration
│   ├── all_tools.py             # All tools in ONE file (stock, analysis, system)
│   ├── stock_tools.py           # Stock data tools (quotes, kline, news, watchlist) - legacy
│   └── analysis_tools.py        # Analysis tools (indicators, anomaly detection) - legacy
│
├── services/                    # Service layer
│   ├── monitoring_service.py    # Real-time monitoring during trading hours
│   ├── morning_report_service.py # Daily morning report (uses SubAgent + Skill)
│   └── screening_service.py     # Stock screening strategies
│
├── skills/                      # Skill layer (Markdown-based skill definitions)
│   ├── morning_report/          # Morning report generation skill
│   │   └── SKILL.md             # Daily pre-market analysis template
│   ├── research_report/         # Research report generation skill
│   │   └── SKILL.md             # Deep stock research analysis template
│   └── alert_analysis/          # Alert analysis skill
│       └── SKILL.md             # Anomaly detection analysis template
│
├── channels/                    # External channel integration
│   ├── __init__.py
│   ├── wecom.py                 # WeCom (企业微信) integration: webhook server + API sender
│   └── feishu.py                # Feishu (飞书) integration: WebSocket + interactive cards
│
├── memory/                      # Persistent storage (跨会话保留)
│   ├── user.md                  # User profile (risk preference, focus sectors, etc.)
│   ├── history.md               # Key event history (重要事件摘要，非完整对话)
│   ├── data.json                # Structured user data
│   └── sessions/                # Conversation sessions (完整对话历史)
│       ├── default.json         # Default session
│       └── {session_id}.json    # Other sessions
│
└── data_store/                  # Runtime data storage
    ├── watchlist.json           # User watchlist
    └── logs/                    # Log files
```

## Common Commands

### Run the System

```bash
cd astack

# Full mode (CLI + monitoring + reports)
python main.py

# CLI only
python main.py --cli

# Generate one-time report
python main.py --report

# Monitoring only
python main.py --monitor

# Use specific session (isolated conversation history)
python main.py --session trading    # Trading analysis session
python main.py --session research   # Research report session
```

### Testing

```bash
# Test new features (SubAgent, Skill, Context Compression)
python test_new_features.py

# Test all tools
python test_tools.py

# Run specific tests (no formal test suite yet)
python -c "from data.akshare_client_fixed import akshare_client; print('OK')"
```

### Dependencies

```bash
# Install dependencies
pip install -r requirements.txt

# Virtual environment (recommended)
python -m venv venv
# Windows: venv\Scripts\activate
# Linux/Mac: source venv/bin/activate
```

## Architecture Patterns

### 1. Singleton Pattern
Multiple components use singleton pattern (instanced at module level):
- `akshare_client` in `data/__init__.py`
- `tool_registry` in `tools/registry.py`
- `user_memory` in `core/memory.py`
- `session_manager` in `core/session.py`
- `notification_manager` in `core/notifier.py`
- `skill_loader`, `subagent_manager`, `context_compressor` in `tools/all_tools.py`
- `monitoring_service`, `morning_report_service`, `screening_service` in services

### 2. Tool Registration Flow
All tools are defined in `tools/all_tools.py` and auto-registered:
1. Define tool class inheriting from `Tool` base class in `all_tools.py`
2. Implement `name`, `description`, `parameters`, `execute()`
3. Add class to `ALL_TOOLS` list in `all_tools.py`
4. Import in `tools/__init__.py` triggers auto-registration via `_register_all_tools()`
5. Agent gets tools via `registry.get_schemas()` for OpenAI function calling

### 3. SubAgent + Skill Pattern
Complex tasks (report generation) use SubAgent with Skill:
```
MorningReportService / GenerateResearchReportTool
    ↓
# For morning reports
subagent_manager.run_with_skill(task, skill_name="morning_report")
# For research reports
subagent_manager.run_with_skill(task, skill_name="research_report")
    ↓
SubAgent (with skill content + tools)
    ↓
LLM with tool calling → Report content
```

### 4. Context Compression
Long conversations are automatically compressed when token threshold is reached:
- Threshold: Configurable via `CONTEXT_COMPRESS_THRESHOLD` in `config.py` (default: 6000)
- Preserves: Stock codes, analysis conclusions, recent messages
- Strategy: Micro-compress (tool results) → Smart compress (LLM summary)
- **Session Integration**: Compression updates `session.last_consolidated`, so history loading starts from compressed position
- SubAgent disables compression by default (short-lived tasks don't need it)

### 5. Memory System (Two-Layer)

#### Layer 1: User Profile (Persistent)
User preferences stored in `memory/user.md` and `memory/data.json`:
- Updated via `user_memory.update_preference()` or `UpdateUserProfileTool`
- Retrieved via `user_memory.get_context()` for agent prompts
- Structured sections: 投资偏好, 自选股票, 关键事件
- **Key Events** (`history.md`): user_profile updates, commands, alerts, system events

#### Layer 2: Session History (Persistent)
Complete conversation history stored in `memory/sessions/{session_id}.json`:
- **Multi-session support**: Use `--session <id>` to create isolated conversations
- **Auto-save**: Every message is saved to JSON file
- **Compression tracking**: `last_consolidated` field tracks which messages were compressed
- **Resume on restart**: Full conversation history is loaded when agent starts

**Key difference from nanobot**:
- `sessions/*.json`: Complete conversation history (all messages)
- `history.md`: Important events only (user profile updates, commands, alerts)

### 6. Agent WORK/IDLE Dual-Phase Architecture

The Agent operates in two phases to optimize resource usage:

```
                    ┌─────────────────────────────────────────┐
                    │                                         │
    ┌───────────┐   │ event   ┌───────────┐   idle   ┌──────┴──┐
    │   IDLE    │◀──┘─────────│   WORK    │─────────▶│  IDLE   │
    │  (等待)   │  ◀──────────│  (工作)   │          │ (等待)  │
    └────┬──────┘   complete  └───────────┘          └─────────┘
         │
         │ 定期检查 event_queue
         │ (interval: AGENT_IDLE_CHECK_INTERVAL seconds)
         ▼
    新事件? ──Yes──▶ 唤醒进入WORK
       │
      No
       │
       ▼
    继续IDLE
```

**Key components**:
- `event_queue: asyncio.Queue[AgentEvent]` - Message queue for event-driven wake-up
- `post_event()` - External interface to wake up Agent from IDLE
- `run_loop()` - Main loop handling WORK/IDLE transitions

**Event types**:
- `user_message` - User input from CLI
- `price_alert` - Anomaly detected by MonitoringService
- `routine_analysis` - Periodic 30-minute analysis trigger
- `shutdown_request` - Graceful shutdown signal

**Implementation** (`core/agent.py`):
```python
async def run_loop(self):
    while not self._shutdown_event.is_set():
        # WORK PHASE
        self._set_status("working")
        # Process events, execute tool calls...
        
        # IDLE PHASE
        self._set_status("idle")
        while not self._shutdown_event.is_set():
            has_event = await self._wait_for_event_or_timeout()
            if has_event:
                break  # Exit IDLE, return to WORK

async def post_event(self, event_type: str, content: str, metadata: dict = None):
    """External interface to wake up Agent from IDLE"""
    event = AgentEvent(event_type=event_type, content=content, metadata=metadata)
    await self.event_queue.put(event)
```

### 7. MonitoringService + Agent Integration

MonitoringService uses a **heartbeat + trigger** pattern:

```
MonitoringService (每分钟心跳)
    │
    ├─ 纯数据检查 (无LLM，低资源)
    │   ├─ 价格变动
    │   ├─ 成交量异常
    │   └─ 技术指标突破
    │
    ├─ 检测到异常? ──Yes──▶ post_event("price_alert") ──▶ 唤醒Agent
    │                                              (使用alert_analysis skill)
    │
    └─ 30分钟无异常? ──Yes──▶ post_event("routine_analysis") ──▶ 唤醒Agent
                                                        (使用research_report skill)
```

**Thresholds** (configurable in `config.py`):
- `PRICE_CHANGE_THRESHOLD = 3.0` - 涨跌幅阈值(%)
- `TICK_DELTA_THRESHOLD = 0.8` - 短时变动阈值(%)
- `VOLUME_RATIO_THRESHOLD = 2.5` - 成交量倍率阈值
- `RSI_OVERBOUGHT = 80` / `RSI_OVERSOLD = 20`
- `ROUTINE_ANALYSIS_INTERVAL = 1800` - 定期分析间隔(30分钟)
- `ALERT_COOLDOWN = 180` - 同一股票告警冷却期(3分钟)

**Implementation** (`services/monitoring_service.py`):
```python
async def _check_all_stocks(self):
    # Pure data checking (no LLM)
    anomalies = self._detect_anomalies(...)
    if anomalies:
        await self._trigger_alert_analysis(code, name, anomalies, quote, state)

async def _trigger_alert_analysis(self, code, name, anomalies, quote, state):
    if self._agent and hasattr(self._agent, 'post_event'):
        await self._agent.post_event(
            "price_alert",
            alert_content,
            metadata={"code": code, "name": name, "anomalies": anomalies}
        )

async def _check_routine_analysis(self):
    if now_ts - self.state.last_routine_analysis_ts >= self.ROUTINE_ANALYSIS_INTERVAL:
        await self._trigger_routine_analysis()

async def _trigger_routine_analysis(self):
    if self._agent and hasattr(self._agent, 'post_event'):
        await self._agent.post_event(
            "routine_analysis",
            task_content,
            metadata={"watchlist": self.watchlist, "trigger": "routine_30min"}
        )
```

### 8. Agent Loop with Multi-Round Tool Calling
Both Main Agent and SubAgent support **multi-round tool calling** within a single user request:

```
User: "分析一下今天的热门股票"

Agent Loop:
├─ Round 1: get_hot_stocks → 获取热门股票列表
├─ Round 2: analyze_stock(股票1) → 获取详细信息
├─ Round 3: get_stock_news → 查询新闻
└─ Round 4: 综合所有数据 → 生成最终回复
```

**Implementation** (`core/agent.py`):
```python
async def chat(self, message: str):
    # Add to session
    self.session.add_message("user", message)

    iteration = 0
    while iteration < max_iterations:
        iteration += 1

        # Compress if needed
        messages = await context_compressor.compress_if_needed(messages, session=self.session)

        # Call LLM
        response = await client.chat.completions.create(...)

        if response.finish_reason != "tool_calls":
            # No tool calls needed, return final answer
            break

        # Execute tool calls and continue loop
        messages, _ = await self._execute_tool_calls_and_record(message_obj, messages)

    # Save final response
    self.session.add_message("assistant", final_response)
    self.session_manager.save(self.session)
```

**SubAgent** (`tools/all_tools.py` `SpawnSubAgentTool`):
- Same loop pattern as Main Agent
- Uses `tools` API (not legacy `functions` API)
- Has its own tool whitelist (`config.SUBAGENT_TOOLS`)

### 9. Scheduling
Uses `schedule` library with asyncio:
- `MonitoringService` runs during trading hours (9:30-11:30, 13:00-15:00)
- `MorningReportService` runs at configurable time (default 09:00)

### 10. User Initialization Flow
On first run (when `user_memory.needs_initialization()` returns True), system guides user through profile setup:

**Fields collected**:
- name: User name/nickname
- risk_preference: conservative/moderate/aggressive
- investment_style: value/growth/trend
- investment_horizon: short/medium/long
- focus_sectors: List of sectors to watch (e.g., "科技,新能源,医药")
- avoid_sectors: List of sectors to avoid
- max_position: Max position per stock (e.g., 20%)
- stop_loss/take_profit: Risk management settings

**Implementation** (`main.py` `AStockSystem._initialize_user_profile()`):
- Interactive prompt asking each field
- Saves to `user_memory` which persists to `user.md` and `data.json`
- Records system event in `history.md` with `event_type="system"`

### 11. CLI Commands

**Command-line flags**:
```bash
python main.py --session trading   # Use specific session
python main.py --cli               # CLI only mode
python main.py --report            # Generate one-time report
python main.py --monitor           # Monitoring only mode
```

**Interactive commands** (input `/help` in CLI):
| Command | Description |
|---------|-------------|
| `/watchlist` | Show watchlist |
| `/add <codes>` | Add stocks to watchlist |
| `/remove <codes>` | Remove stocks from watchlist |
| `/report` | Generate morning report |
| `/monitor` | Start monitoring service |
| `/stop` | Stop monitoring service |
| `/clear` | Clear conversation history |
| `/profile` | View user profile |
| `/update <field> <value>` | Update user profile field |
| `/session` | View current session info |
| `exit` | Exit program |

## Configuration

Configuration flows: `.env` → `config.py` → `config` instance

Key environment variables in `.env`:
```
OPENAI_API_KEY=xxx
OPENAI_MODEL=gpt-4o
# Moonshot kimi-k2.5 requires temperature=1.0
OPENAI_TEMPERATURE=0.7

# Monitoring
MONITOR_INTERVAL=30
MORNING_REPORT_TIME=09:00

# Context compression threshold (tokens)
CONTEXT_COMPRESS_THRESHOLD=6000

# Optional notifications
NOTIFICATION_CHANNELS=console,dingtalk
DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=xxx
```

Key configuration in `config.py`:
- `MAIN_AGENT_TOOLS`: Whitelist of tools available to main agent (empty = all)
- `SUBAGENT_TOOLS`: Whitelist of tools available to sub-agent
- `MAIN_AGENT_SKILLS` / `SUBAGENT_SKILLS`: Whitelist of skills for each agent type
- `CONTEXT_COMPRESS_THRESHOLD`: Token threshold for context compression
- `AGENT_IDLE_CHECK_INTERVAL`: Seconds between IDLE phase event queue checks (default: 5)
- `AGENT_IDLE_MAX_DURATION`: Max IDLE duration before forcing periodic task (0 = unlimited)
- `AGENT_MAX_WORK_ITERATIONS`: Max iterations in WORK phase before forcing idle
- `ROUTINE_ANALYSIS_INTERVAL`: Seconds between periodic analysis (default: 1800 = 30min)
- `ALERT_COOLDOWN`: Seconds between repeated alerts for same stock (default: 180 = 3min)

## Important Implementation Details


### Moonshot/kimi-k2.5 API 注意事项

当使用 Moonshot API (kimi-k2.5) 时，需要特别注意以下几点：

#### 1. Temperature 设置
Moonshot kimi-k2.5 只支持 `temperature=1.0`，其他值会报错：
```python
if "kimi" in self.model.lower():
    self.temperature = 1.0  # Moonshot requires temperature=1
```

#### 2. Reasoning Content 必须保留
**重要**：当启用 thinking 模式时，Moonshot API 要求所有助手消息（assistant message）必须保留 `reasoning_content` 字段，否则会返回 400 Bad Request 错误：

```
Error: thinking is enabled but reasoning_content is missing in assistant tool call message
```

**正确处理示例**（主Agent和子Agent都需要）：
```python
# 调用LLM后获取响应
response = await client.chat.completions.create(...)
message = response.choices[0].message

# 构建助手消息时，必须包含 reasoning_content
assistant_msg = {
    "role": "assistant",
    "content": message.content or "",
    "tool_calls": [tc.model_dump() for tc in message.tool_calls]
}

# 关键：必须获取并保留 reasoning_content
reasoning = getattr(message, 'reasoning_content', None)
if reasoning:
    assistant_msg["reasoning_content"] = reasoning

# 将完整消息添加到消息列表
messages.append(assistant_msg)
```

**常见错误**：
- ❌ 错误：只在本地消息中添加 content 和 tool_calls，遗漏 reasoning_content
- ❌ 错误：将消息保存到历史记录时移除了 reasoning_content 字段
- ✅ 正确：发送给 API 的消息必须包含所有原始字段，包括 reasoning_content

#### 3. 使用标准 Tools API（非 Functions API）
主Agent和子Agent统一使用 OpenAI 标准的 `tools`/`tool_calls` API：
```python
# 正确的调用方式
response = await client.chat.completions.create(
    model=model,
    messages=messages,
    tools=tool_schemas,        # 不是 functions
    tool_choice="auto",        # 不是 function_call
)

# 检查工具调用
if response.choices[0].finish_reason == "tool_calls":  # 不是 message.function_call
    # 处理 tool_calls
    for tc in message.tool_calls:
        # tc.function.name, tc.function.arguments
        pass

# 添加工具结果
messages.append({
    "role": "tool",
    "tool_call_id": tc.id,    # 必须指定 tool_call_id
    "content": result_content
})
```

#### 4. 文件位置
涉及 Moonshot 兼容性的代码主要分布在：
- `core/agent.py`: 主Agent的 `_execute_tool_calls_and_record()` 方法
- `tools/all_tools.py`: 子Agent的 `SpawnSubAgentTool.execute()` 方法

**修改任何Agent循环代码时，务必确保 reasoning_content 被正确处理！**

### MonitoringService + Agent Connection

The connection between MonitoringService and Agent is established in `scheduler.py`:

```python
class TaskScheduler:
    def __init__(self, agent):
        self.agent = agent
        # Set agent reference in monitoring service
        monitoring_service.set_agent(agent)
```

This allows MonitoringService to trigger Agent via `post_event()`. If Agent is not connected:
- MonitoringService falls back to direct notification (without Agent analysis)
- Periodic analysis uses `_fallback_routine_analysis()` to call SubAgent directly

### Event Type Handling

When adding new event types, update both:
1. `AgentEvent.event_type` dataclass in `core/agent.py`
2. Event handling logic in `run_loop()` method
3. System prompt guidance for the Agent

Example:
```python
# In core/agent.py _build_system_prompt()
"""
### X. New Event Type (new_event_type)
**Trigger condition**: Description of when this triggers
**Processing flow**:
- Step 1
- Step 2
- Call spawn_subagent with skill_name="xxx"
"""
```

### Trading Hours Detection
`scheduler.py` checks Chinese trading hours:
- Morning: 09:30 - 11:30
- Afternoon: 13:00 - 15:00
- Weekdays only (Monday-Friday)

## File Relationships

- `main.py` → imports all services and starts event loop, creates AStockAgent with session_id, starts scheduler
- `core/agent.py` → AStockAgent with WORK/IDLE dual-phase, event queue, tool calling
- `core/session.py` → manages conversation persistence in `memory/sessions/`
- `core/memory.py` → manages user profile in `user.md` and key events in `history.md`
- `core/scheduler.py` → TaskScheduler with schedule library, connects MonitoringService to Agent
- `services/monitoring_service.py` → heartbeat monitoring, triggers Agent via post_event()
- `services/morning_report_service.py` → morning report using SubAgent + morning_report skill
- `data/akshare_client_fixed.py` → AKShare client with retry/rate-limiting
- `tools/all_tools.py` → all tools (stock, analysis, system), SubAgentManager, SkillLoader
- `tools/registry.py` → ToolRegistry for tool registration and execution
- `skills/` → Markdown-based skill definitions for SubAgent guidance

## Adding New Features

### New Tool (in all_tools.py)
1. Add tool class to `tools/all_tools.py` inheriting from `Tool`
2. Implement required properties and `execute()` method
3. Add class to `ALL_TOOLS` list at the bottom of `all_tools.py`
4. Import in `tools/__init__.py` (auto-registration happens automatically)

### UpdateUserProfileTool
Tool for updating user profile from agent conversations:
```python
from tools.all_tools import UpdateUserProfileTool

tool = UpdateUserProfileTool()
result = await tool.run(
    field="risk_preference",  # name, risk_preference, investment_style, focus_sectors, etc.
    value="conservative",
    reason="User explicitly stated conservative preference"
)
# This updates user_memory and appends to history.md with event_type="user_profile"
```

**Usage timing**:
- Weekly summary (auto-update based on conversation patterns)
- When user explicitly states preference changes
- NOT for every conversation (avoid over-updating)

### Session Management
Access conversation history and manage sessions:
```python
from core.session import session_manager, Session

# Get or create session (auto-loads from file)
session = session_manager.get_or_create("trading")

# Access messages
history = session.get_history(max_messages=20)  # From last_consolidated position

# Manual save
session_manager.save(session)

# Get session info
info = {
    "session_id": session.session_id,
    "total_messages": len(session.messages),
    "last_consolidated": session.last_consolidated,
    "compress_count": session.compress_count
}
```

**Key fields**:
- `session.messages`: All messages (including compressed ones)
- `session.last_consolidated`: Index of first uncompressed message
- `session.get_history()`: Returns only active (non-compressed) messages for LLM

### New Service
1. Create in `services/`, use singleton pattern
2. Import and start in `main.py` or `scheduler.py`
3. Use `notification_manager` for output

### New Indicator
1. Add calculation in `data/indicators.py`
2. Add detection logic in `tools/analysis_tools.py` `detect_anomalies()`
3. Update thresholds in `config.py` if needed

### New Skill
1. Create folder in `skills/<skill_name>/`
2. Create `SKILL.md` with YAML frontmatter:
   ```yaml
   ---
   name: skill_name
   description: Skill description
   version: 1.0
   tags: tag1, tag2
   ---
   # Skill content...
   ```
3. Use via `skill_loader.load("skill_name")` or `subagent_manager.run_with_skill()`

### Using SubAgent
```python
from tools.all_tools import subagent_manager

# For morning reports
result = await subagent_manager.run_with_skill(
    task_name="morning_report",
    task_description="Generate morning market report...",
    skill_name="morning_report",
    context={"date": "2024-01-01"},
    use_global_tools=True
)

# For research reports
result = await subagent_manager.run_with_skill(
    task_name="research_600036",
    task_description="Generate research report for 600036...",
    skill_name="research_report",
    context={"code": "600036"},
    use_global_tools=True
)
report_content = result.get("content")
```

### New System Tools (spawn_subagent, load_skill, compress_context, idle)
```python
# Spawn a subagent for complex tasks
spawn_subagent(
    task_name="research_600036",
    task_description="Generate research report for 600036",
    skill_name="research_report"  # or "alert_analysis", "morning_report"
)

# Load a skill's full content
load_skill(skill_name="research_report")  # or "morning_report", "alert_analysis"

# Manually trigger context compression
compress_context(force=False)

# Enter IDLE state (called by Agent when work is done)
idle(reason="任务完成，进入待机")
```

### Alert Analysis Flow
When MonitoringService detects an anomaly, it triggers the following flow:

```
MonitoringService._trigger_alert_analysis()
    ↓
agent.post_event("price_alert", content, metadata)
    ↓
AStockAgent.run_loop() [WORK PHASE]
    ↓
spawn_subagent(
    task_name=f"alert_{code}_{timestamp}",
    task_description=alert_content,
    skill_name="alert_analysis"  # Uses alert_analysis skill
)
    ↓
SubAgent executes with alert_analysis SKILL.md guidance
    ↓
Returns analysis report with severity rating and recommendations
    ↓
Main Agent displays report to user
    ↓
idle()  # Return to IDLE state
```

### Periodic Analysis Flow
Every 30 minutes without anomalies:

```
MonitoringService._trigger_routine_analysis()
    ↓
agent.post_event("routine_analysis", task_content, metadata)
    ↓
AStockAgent.run_loop() [WORK PHASE]
    ↓
spawn_subagent(
    task_name=f"routine_{timestamp}",
    task_description=task_content,
    skill_name="research_report"
)
    ↓
SubAgent analyzes all watchlist stocks
    ↓
Returns comprehensive analysis with recommendations
    ↓
Main Agent decides whether to notify user
    ↓
idle()  # Return to IDLE state
```

### Event Queue Usage
To trigger Agent from external services:

```python
# In MonitoringService or other services
from core.agent import AStockAgent

# Set agent reference
monitoring_service.set_agent(agent)

# Trigger from anywhere
await agent.post_event(
    event_type="price_alert",      # or "routine_analysis", "user_message"
    content="Detailed description",
    metadata={"key": "value"}      # Optional additional data
)
```

**Event handling in Agent** (`core/agent.py`):
```python
for event in inbox_events:
    if event.event_type == "price_alert":
        # Add system hint to use alert_analysis skill
        messages.append({
            "role": "system",
            "content": "【系统事件: price_alert】... spawn_subagent skill_name='alert_analysis'"
        })
    elif event.event_type == "routine_analysis":
        messages.append({
            "role": "system",
            "content": "【系统事件: routine_analysis】... spawn_subagent skill_name='research_report'"
        })
```

## WeCom (企业微信) Integration

### Architecture

The system supports bidirectional communication with WeCom (Enterprise WeChat):

```
WeCom User
    ↓ (send message)
WeCom Server
    ↓ (POST to webhook)
Webhook Server (channels/wecom.py)
    ↓
Write to Mailbox (memory/mailbox/wecom_xxx.json)
    ↓
Agent detects event and processes
    ↓
Agent generates reply
    ↓
Send via WeCom API (channels/wecom.py)
    ↓
WeCom User receives message
```

### Configuration

Required environment variables in `.env`:
```
WECOM_CORP_ID=xxxxxxxxxxxxxxxx
WECOM_CORP_SECRET=xxxxxxxxxxxxxxxx
WECOM_AGENT_ID=1000002
WECOM_WEBHOOK_PORT=8080
WECOM_TOKEN=your_token              # Optional, for URL verification
WECOM_ENCODING_AES_KEY=your_key     # Optional, for message encryption
WECOM_REPORT_USERS=ZhangSan,LiSi    # Users to receive morning reports
WECOM_ALERT_USERS=ZhangSan,LiSi     # Users to receive alerts
```

### Components

1. **WeComChannel** (`channels/wecom.py`)
   - `start_webhook_server()`: Starts aiohttp server to receive WeCom messages
   - `_handle_receive_message()`: Parses XML message and writes to Mailbox
   - `send_message()`: Sends reply via WeCom API using access token
   - `_get_access_token()`: Manages token caching and refresh

2. **Agent Integration** (`core/agent.py`)
   - `_current_wecom_user`: Tracks which WeCom user sent the current message
   - `_drain_mailbox()`: Detects `wecom_message` events and records user ID
   - Reply automatically sent to WeCom if `_current_wecom_user` is set

3. **Service Integration**
   - `MorningReportService`: Sends reports to `WECOM_REPORT_USERS`
   - `MonitoringService`: Sends alerts to `WECOM_ALERT_USERS`

### Message Flow

**Incoming (WeCom → Agent)**:
```python
# In WeComChannel._write_to_mailbox()
event_data = {
    "event_type": "wecom_message",
    "content": msg.content,
    "metadata": {
        "from_user": msg.from_user,  # WeCom user ID
        "channel": "wecom"
    }
}
```

**Outgoing (Agent → WeCom)**:
```python
# In Agent.run_loop() - after generating reply
if self._current_wecom_user and message_obj.content:
    await wecom_channel.send_message(
        self._current_wecom_user,
        message_obj.content
    )
    self._current_wecom_user = None  # Clear after sending
```

### Silent Mode

Agent can choose not to send messages by including `[SKIP_SEND]` in the content:
```python
if "[SKIP_SEND]" in content:
    logger.info("Agent marked as skip send")
    return
```

### Setup Instructions

1. Create application in [WeCom Admin Console](https://work.weixin.qq.com/)
2. Record AgentId and Secret
3. Set "Receive Message" URL to: `http://your-server:8080/wecom/webhook`
4. Configure Token and EncodingAESKey (optional but recommended)
5. Add environment variables to `.env` file

## Feishu (飞书) Integration

### Architecture

Feishu integration uses WebSocket long connection for bidirectional communication:

```
Feishu User
    ↓ (send message)
Feishu Server
    ↓ (WebSocket push)
FeishuChannel (channels/feishu.py)
    ↓
Write to Mailbox (memory/mailbox/feishu_xxx.json)
    ↓
Agent detects event and processes
    ↓
Agent generates reply
    ↓
Send via Feishu API (channels/feishu.py)
    ↓
Feishu User receives message
```

### Key Differences from WeCom

| Aspect | WeCom | Feishu |
|--------|-------|--------|
| Connection | HTTP Webhook | WebSocket |
| Public IP | Required | Not required |
| SDK | Manual HTTP calls | `lark-oapi` SDK |
| Message format | Markdown | Interactive Cards |
| Reaction | Not supported | Supported (👍) |

### Configuration

Required environment variables in `.env`:
```
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
FEISHU_ENCRYPT_KEY=                 # Optional
FEISHU_VERIFICATION_TOKEN=          # Optional
FEISHU_REPORT_USERS=ou_xxx,ou_yyy   # Morning report recipients
FEISHU_ALERT_USERS=ou_xxx,ou_yyy    # Alert recipients
```

### Components

1. **FeishuChannel** (`channels/feishu.py`)
   - `start()`: Creates WebSocket connection using `lark-oapi` SDK
   - `_on_message()`: Handles incoming messages from WebSocket
   - `_write_to_mailbox()`: Writes events to filesystem mailbox
   - `send_message()`: Sends replies via Feishu API
   - `_add_reaction()`: Adds emoji reaction to acknowledge message

2. **Agent Integration** (`core/agent.py`)
   - `_current_feishu_chat_id`: Tracks current Feishu conversation
   - `_drain_mailbox()`: Detects `feishu_message` events
   - Reply automatically sent to Feishu if `_current_feishu_chat_id` is set

3. **Service Integration**
   - `MorningReportService`: Sends reports to `FEISHU_REPORT_USERS`
   - `MonitoringService`: Sends alerts to `FEISHU_ALERT_USERS`

### WebSocket Implementation

Unlike WeCom's HTTP webhook, Feishu uses WebSocket:

```python
# In FeishuChannel.start()
self._ws_client = lark.ws.Client(
    self.app_id,
    self.app_secret,
    event_handler=event_handler,
    log_level=lark.LogLevel.WARN
)

# Run in separate thread with reconnection
self._ws_thread = threading.Thread(target=run_ws, daemon=True)
self._ws_thread.start()
```

### Message Flow

**Incoming (Feishu → Agent)**:
```python
# In FeishuChannel._write_to_mailbox()
event_data = {
    "event_type": "feishu_message",
    "content": msg.content,
    "metadata": {
        "from_user": msg.from_user,    # User's open_id
        "chat_id": msg.chat_id,        # Chat/Group ID
        "chat_type": msg.chat_type,    # p2p or group
        "channel": "feishu"
    }
}
```

**Outgoing (Agent → Feishu)**:
```python
# In Agent.run_loop() - after generating reply
if self._current_feishu_chat_id and message_obj.content:
    await feishu_channel.send_message(
        self._current_feishu_chat_id,
        message_obj.content,
        msg_type="interactive"  # Feishu uses interactive cards
    )
```

### Setup Instructions

1. Create "Custom App" in [Feishu Open Platform](https://open.feishu.cn/)
2. Record App ID and App Secret
3. Enable "Bot" capability
4. Subscribe to `im.message.receive_v1` event
5. Publish app and apply for permissions
6. Install dependencies: `pip install lark-oapi`
7. Add environment variables to `.env` file
