# 📈 A股分析Agent系统

一个基于LLM和AKShare的智能A股分析系统，提供实时监控、每日报告和对话式分析功能。

## ✨ 功能特性

### 1. 实时技术监控 + Agent智能分析 ⏰
- **双模式监控架构**：
  - **心跳模式**：每分钟纯数据检查自选股（无LLM，低资源消耗）
  - **触发模式**：检测到异常时，通过**消息队列**唤醒主Agent进行深度分析
- **定期分析**：每30分钟无异常时，自动唤醒Agent分析所有自选数据，决定是否需要提醒
- **自动检测技术指标异常**：
  - 价格异动（涨跌幅 > 3%/5%）
  - 成交量异常放大（量比 > 2.5x）
  - 短时快速变动（分钟内变动 > 0.8%）
  - RSI超买超卖（>80/<20）
  - BOLL布林带突破
- 发现异常立即通知用户并给出投资建议

### 2. Agent Idle/工作模式 🤖
- **WORK PHASE**：处理用户对话、执行工具调用、分析股票
- **IDLE PHASE**：无任务时进入挂机状态，节省资源
- **消息队列驱动**：通过 `post_event()` 从外部唤醒Agent
  - 用户消息
  - 股价异常告警 (`price_alert`)
  - 定期分析任务 (`routine_analysis`)
- 状态自动持久化，重启后恢复

### 3. 子智能体 + Skill 架构 🎯
- **主Agent**：处理用户交互，协调任务分配
- **子Agent (SubAgent)**：执行复杂分析任务
  - 深度调研报告 (`research_report` skill)
  - 异常告警分析 (`alert_analysis` skill)
  - 每日晨报生成 (`morning_report` skill)
- **Skill系统**：Markdown格式定义分析模板，可扩展

### 4. 早间市场报告 📰
- 每天开市前自动生成市场分析报告
- 使用子Agent + `morning_report` skill 生成专业报告
- 内容包括：
  - 市场情绪分析（涨跌家数、涨跌停）
  - 自选股票技术分析
  - 热门股票调研
  - 板块资金流向
  - 投资建议

### 5. 对话式分析 💬
- 自然语言交互，分析任意股票
- 智能工具调用，自动获取数据
- 支持**多轮工具调用**：先获取数据 → 分析 → 决定是否需要更多信息
- 技术分析、新闻分析、综合评估

### 6. 上下文压缩 🗜️
- 长对话自动压缩，保留关键信息
- 压缩策略：
  - 保留最近3个工具调用完整内容
  - 更早的工具调用转为占位符
  - 使用LLM智能总结股票相关信息
- Token阈值可配置（默认6000）

### 7. 用户记忆系统 🧠
- 双层记忆架构：
  - **用户画像** (`memory/user.md`)：风险偏好、投资风格、关注领域、交易规则
  - **会话历史** (`memory/sessions/{session_id}.json`)：完整对话记录，支持多会话
  - **关键事件** (`memory/history.md`)：重要操作记录
- 分析时自动融入用户偏好
- 支持多会话隔离（`--session <id>`）

### 5. 智能选股 🔍
- 多维度选股策略：
  - 技术面选股
  - 动量选股
  - 价值投资选股
  - 突破选股

## 🚀 快速开始

### 安装依赖

```bash
# 创建虚拟环境（推荐）
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt
```

### 配置环境变量

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env 文件，设置您的OpenAI API密钥
OPENAI_API_KEY=your_api_key_here
```

### 启动系统

```bash
# 启动完整服务
python main.py

# 仅启动命令行交互
python main.py --cli

# 仅生成一次报告
python main.py --report

# 仅启动监控服务
python main.py --monitor
```

## 📖 使用指南

### 命令行交互

```
👤 您: 分析600036
🤖 AI: [股票分析报告...]

👤 您: 查看招商银行
🤖 AI: [实时行情...]

👤 您: 添加000001到自选
🤖 AI: 成功添加 000001 到自选...
```

### 快捷命令

| 命令 | 说明 |
|------|------|
| `/watchlist` | 显示自选列表 |
| `/add <代码>` | 添加股票到自选 |
| `/remove <代码>` | 从自选移除 |
| `/report` | 生成早间报告 |
| `/monitor` | 启动监控 |
| `/stop` | 停止监控 |
| `/clear` | 清除对话历史 |
| `help` | 显示帮助 |
| `exit` | 退出程序 |

### 自选股票管理

```
👤 您: /add 600036 000001 300750
🤖 AI: 成功添加 3 只股票到自选，当前共 5 只

👤 您: /watchlist
🤖 AI: 
⭐ 自选列表:
  • 600036
  • 000001
  • 300750
```

## 🏗️ 项目结构

```
astack/
├── main.py                      # 主入口
├── config.py                    # 配置文件
├── requirements.txt             # 依赖列表
├── .env.example                 # 环境变量模板
│
├── core/                        # 核心模块
│   ├── agent.py                 # AStockAgent - 主Agent（WORK/IDLE双模式）
│   ├── memory.py                # 用户记忆系统
│   ├── session.py               # 会话管理（多会话支持）
│   ├── scheduler.py             # 任务调度（定时任务）
│   ├── notifier.py              # 通知管理
│   ├── subagent.py              # 子Agent管理器
│   ├── skill_loader.py          # Skill加载器
│   └── context_compressor.py    # 上下文压缩器
│
├── data/                        # 数据模块
│   ├── akshare_client.py        # AKShare客户端
│   ├── akshare_client_fixed.py  # AKShare客户端（增强版）
│   └── indicators.py            # 技术指标计算
│
├── tools/                       # 工具模块
│   ├── base.py                  # 工具基类
│   ├── registry.py              # 工具注册表
│   └── all_tools.py             # 所有工具（股票/分析/系统工具）
│
├── services/                    # 服务模块
│   ├── monitoring_service.py    # 实时监控服务（心跳机制）
│   ├── morning_report_service.py # 早间报告服务
│   └── screening_service.py     # 选股服务
│
├── skills/                      # Skill定义（Markdown格式）
│   ├── morning_report/SKILL.md  # 晨报生成模板
│   ├── research_report/SKILL.md # 深度研报模板
│   └── alert_analysis/SKILL.md  # 异常告警分析模板
│
├── channels/                    # 外部渠道接入
│   ├── __init__.py
│   └── wecom.py                 # 企业微信接入（Webhook服务器 + API发送）
│
└── memory/                      # 持久化存储
    ├── user.md                  # 用户画像
    ├── history.md               # 关键事件历史
    ├── data.json                # 结构化用户数据
    └── sessions/                # 会话历史
        ├── default.json
        └── {session_id}.json
```

## 🔧 配置说明

### 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `OPENAI_API_KEY` | OpenAI API密钥 | 必需 |
| `OPENAI_API_BASE` | API基础地址 | https://api.openai.com/v1 |
| `OPENAI_MODEL` | 使用的模型 | gpt-4o |
| `OPENAI_TEMPERATURE` | 温度参数 | 0.7 |
| `MONITOR_INTERVAL` | 监控间隔（秒） | 60 |
| `ROUTINE_ANALYSIS_INTERVAL` | 定期分析间隔（秒） | 1800 (30分钟) |
| `ALERT_COOLDOWN` | 告警冷却期（秒） | 180 |
| `MORNING_REPORT_TIME` | 早间报告时间 | 09:00 |
| `CONTEXT_COMPRESS_THRESHOLD` | 上下文压缩阈值（token） | 6000 |

### 用户记忆 (user.md)

系统自动维护 `user.md` 文件，您也可以手动编辑：

```markdown
## 投资偏好
- **风险偏好**: moderate
- **投资风格**: value
- **关注领域**: 科技股、新能源
- **止损线**: 10%
```

## 📊 技术指标说明

### 支持的指标

| 指标 | 说明 |
|------|------|
| MA | 移动平均线 (MA5, MA10, MA20, MA60) |
| RSI | 相对强弱指标 (RSI6, RSI14) |
| MACD | 指数平滑异同平均线 |
| BOLL | 布林带 |
| KDJ | 随机指标 |
| ATR | 平均真实波幅 |

### 异常检测

| 异常类型 | 触发条件 | 严重程度 |
|----------|----------|----------|
| 价格异动 | 涨跌幅 >= 5% | 🔴 高 |
| 价格变动 | 涨跌幅 >= 3% | 🟡 中 |
| 短时变动 | 分钟内变动 >= 0.8% | 🟡 中 |
| 成交量异常 | 量比 >= 2.5x | 🟡 中 |
| RSI超买 | RSI >= 80 | 🟡 中 |
| RSI超卖 | RSI <= 20 | 🟡 中 |
| BOLL上轨突破 | 价格突破布林带上轨 | 🔴 高 |
| BOLL下轨突破 | 价格跌破布林带下轨 | 🔴 高 |

### 告警触发机制

1. **数据检查**（每分钟，无LLM）
   - 纯数据计算涨跌幅、成交量、技术指标
   - 对比阈值判断是否异常

2. **触发Agent分析**（检测到异常时）
   - 通过消息队列 `post_event("price_alert", ...)` 唤醒主Agent
   - 主Agent调用子Agent + `alert_analysis` skill 进行深度分析
   - 分析完成后主动提醒用户

3. **定期分析**（每30分钟无异常时）
   - 通过消息队列 `post_event("routine_analysis", ...)` 唤醒主Agent
   - 主Agent调用子Agent + `research_report` skill 分析所有自选
   - 决定是否需要提醒用户

## 🔔 通知配置

### 控制台通知（默认）
无需配置，直接在终端显示

### 钉钉通知
在 `.env` 中配置：
```
NOTIFICATION_CHANNELS=console,dingtalk
DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=xxx
DINGTALK_SECRET=your_secret
```

### 企业微信通知（支持双向通信）

系统支持与企业微信**双向通信**，用户可以通过企业微信发送消息与Agent交互。

#### 1. 配置企业微信应用

在 `.env` 中配置：
```bash
# 基础信息（从企业微信管理后台获取）
WECOM_CORP_ID=xxxxxxxxxxxxxxxx
WECOM_CORP_SECRET=xxxxxxxxxxxxxxxx
WECOM_AGENT_ID=1000002

# Webhook服务器端口
WECOM_WEBHOOK_PORT=8080

# Token和EncodingAESKey（用于消息验证和加密，可选但建议配置）
WECOM_TOKEN=your_token
WECOM_ENCODING_AES_KEY=your_aes_key

# 发送目标用户（多个用逗号分隔）
WECOM_REPORT_USERS=ZhangSan,LiSi
WECOM_ALERT_USERS=ZhangSan,LiSi
```

#### 2. 企业微信后台配置

1. 登录 [企业微信管理后台](https://work.weixin.qq.com/)
2. 进入「应用管理」-「创建应用」
3. 记录 **AgentId** 和 **Secret**
4. 设置「接收消息」的URL为：`http://your-server:8080/wecom/webhook`
5. 设置Token和EncodingAESKey（与.env配置一致）

#### 3. 支持的交互

**用户 → Agent**：
- 发送股票代码查询行情
- 发送"分析600036"获取分析报告
- 发送"/report"生成晨报

**Agent → 用户**：
- 晨报每天自动发送（可配置）
- 股票监控预警实时推送
- 定期分析报告（30分钟）

#### 4. 静默模式

如果Agent判断一切正常，可以在回复中添加 `[SKIP_SEND]` 标记来跳过发送：
```python
# Agent可以决定不发送某些消息
if no_significant_changes:
    return "[SKIP_SEND] 市场正常，无重要变化"
```

### 飞书通知（支持双向通信）

系统支持飞书**双向通信**，使用 WebSocket 长连接，无需公网IP。

#### 1. 配置飞书应用

在 `.env` 中配置：
```bash
# 基础信息（从飞书开放平台获取）
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 可选：消息加密和验证
FEISHU_ENCRYPT_KEY=
FEISHU_VERIFICATION_TOKEN=

# 发送目标用户（多个用逗号分隔，使用用户的open_id）
FEISHU_REPORT_USERS=ou_xxxxxxxxxxxxxxxx
FEISHU_ALERT_USERS=ou_xxxxxxxxxxxxxxxx
```

#### 2. 飞书开放平台配置

1. 登录 [飞书开放平台](https://open.feishu.cn/)
2. 创建「企业自建应用」
3. 进入「凭证与基础信息」，记录 **App ID** 和 **App Secret**
4. 开启「机器人」功能
5. 在「事件订阅」中开启以下事件：
   - `im.message.receive_v1`（接收消息）
6. 无需配置请求地址（使用 WebSocket 连接）
7. 发布应用并申请权限

#### 3. 支持的交互

与企业微信相同：
- 用户发送股票代码查询行情
- Agent回复分析报告
- 晨报和预警自动推送

#### 4. 静默模式

与企业微信相同，Agent可以添加 `[SKIP_SEND]` 标记跳过发送。

## 📊 支持的渠道对比

| 功能 | 企业微信 | 飞书 |
|------|---------|------|
| 双向通信 | ✅ | ✅ |
| WebSocket | ❌ | ✅ |
| 需要公网IP | ✅（Webhook） | ❌ |
| 文本消息 | ✅ | ✅ |
| Markdown/卡片 | ✅ | ✅ |
| 反应表情 | ❌ | ✅（👍）|

## 🛠️ 开发指南

### Agent工作模式

系统采用 **WORK/IDLE 双阶段** 架构：

```
IDLE PHASE          WORK PHASE           IDLE PHASE
   (等待)    ──事件──▶ (处理任务)  ──完成──▶ (等待)
     ▲                                          │
     └──────────── 新事件触发 ──────────────────┘
```

- **IDLE阶段**：Agent处于挂机状态，定期检查事件队列
- **WORK阶段**：处理用户消息、执行工具调用、生成分析报告
- **状态切换**：通过 `post_event()` 方法从外部触发

### 添加新工具

在 `tools/all_tools.py` 中添加：

```python
class MyTool(Tool):
    @property
    def name(self) -> str:
        return "my_tool"

    @property
    def description(self) -> str:
        return "工具描述"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "param1": {"type": "string"}
            },
            "required": ["param1"]
        }

    async def execute(self, **kwargs) -> ToolResult:
        # 实现逻辑
        return ToolResult(success=True, data={...})

# 添加到 ALL_TOOLS 列表
ALL_TOOLS = [..., MyTool]
```

### 添加新Skill

在 `skills/{skill_name}/SKILL.md` 创建：

```markdown
---
name: skill_name
description: Skill描述
version: 1.0
tags: tag1, tag2
---

# Skill内容

## 分析流程
1. 步骤1
2. 步骤2

## 输出格式
...
```

然后在 `config.py` 添加到白名单：

```python
SUBAGENT_SKILLS: List[str] = field(default_factory=lambda: [
    "research_report",
    "morning_report",
    "alert_analysis",
    "your_new_skill",  # 添加到这里
])
```

### 通过消息队列唤醒Agent

```python
from core.agent import AStockAgent

agent = AStockAgent(session_id="default")

# 发送事件唤醒Agent
await agent.post_event(
    event_type="price_alert",  # 或 "routine_analysis", "user_message"
    content="告警内容",
    metadata={"code": "600036", "price": 10.5}
)
```

### 子Agent调用

```python
from tools.all_tools import subagent_manager

result = await subagent_manager.run_with_skill(
    task_name="analysis_task",
    task_description="分析任务描述",
    skill_name="alert_analysis",  # 使用的Skill
    context={"code": "600036"},
    use_global_tools=True
)

content = result.get("content")
```

## 📄 许可证

MIT License

## 🤝 致谢

- [AKShare](https://www.akshare.xyz/) - 财经数据接口
- [nanobot](https://github.com/...) - Agent框架参考

## ⚠️ 免责声明

本系统仅供学习和研究使用，不构成投资建议。股市有风险，投资需谨慎。
