# 股伴 StockMate

一个面向个人的 A 股研究 Agent：通过桌面 Web 对话获取行情、研究个股、生成简报，并记住你的投资偏好、按计划继续研究。

React / TypeScript 前端与 Python / aiohttp 后端，复用同一套主 Agent、工具、Skill 和飞书接入。本机、单用户运行，不提供交易执行功能。

## 功能

- **对话研究**：真实工具调用、多轮追问、Markdown 表格和代码展示；执行状态通过 SSE 更新，正文生成完成后显示。
- **我的自选**：显示股票名称、代码和备注。按钮直接查工具，卡片保留在网页记录中，不进入模型上下文。
- **每日简报**：执行现有 `morning_report` Skill，报告可继续追问；另有个股研究与异动分析 Skill。
- **看大盘**：展示涨跌家数、涨跌停及市场情绪，不额外请求模型总结。
- **定时任务**：手动或通过自然语言创建单次、每天、工作日任务，查看下次时间、暂停、启用或删除。
- **用户记忆**：保存风险偏好、投资风格和关注行业；聊天与上下文摘要在重启后恢复。
- **渠道接入**：支持飞书和企业微信，回复按任务来源发送，网页回复不会自动误发到飞书。

行情、K 线、新闻和市场情绪来自公开数据源；接口错误会明确返回，不以空数据冒充成功。选股工具筛选的是成交额前 200 名，不是全市场扫描。页面背景 K 线仅为装饰。

## 快速开始

已验证环境：Python 3.12、Node.js 24。建议使用 Python 3.12 和 Node.js 22+（包含 npm）。

### 1. 安装

```powershell
git clone https://github.com/XUhy299/stockmate.git
cd stockmate
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
npm --prefix front ci
```

已有本地项目可以继续使用原来的 `astack` 文件夹和虚拟环境，不需要搬动；不要覆盖已有 `.env` 和用户数据。

### 2. 配置模型

编辑 `.env`，填写服务商提供的 OpenAI 兼容密钥、兼容接口地址和可用模型。当前联调使用 `qwen3.8-flash`，其余设置示例：

```dotenv
OPENAI_API_KEY=your-api-key
OPENAI_API_BASE=https://your-provider.example/compatible-mode/v1
OPENAI_MODEL=qwen3.8-flash
OPENAI_TEMPERATURE=0.6
OPENAI_THINKING=disabled
OPENAI_REQUESTS_PER_MINUTE=0
CONTEXT_COMPRESS_THRESHOLD=20000
```

上述密钥和域名是占位内容，必须换成自己的配置；如服务商提供专属业务空间地址，应使用该地址。更换模型时按服务商要求设置参数。密钥只在后端读取。

### 3. 构建并启动

```powershell
npm --prefix front run build
.\venv\Scripts\python.exe main.py --web
```

打开 **http://127.0.0.1:8088**。完成首次构建后，也可以双击 `start-web.bat`。前端构建产物不提交到仓库，新克隆项目需要自行构建。

修改后端代码或 `.env` 后先按 `Ctrl+C` 退出，再启动；修改前端后重新构建并刷新页面。退出时关闭调度、Agent 和渠道连接。

## 使用方式

### 网页演示

1. 输入“分析一下平安银行”，展开执行状态查看工具调用。
2. 追问“刚才那只股票的主要风险是什么”，展示多轮上下文。
3. 点击“我的自选”和“看大盘”，查看已有数据。
4. 点击“每日简报”，等待报告后针对其中一点追问。
5. 点击“定时任务”，或发送“十分钟后分析平安银行”，再到面板查看任务。

可以说“记住，我偏好稳健投资，主要关注银行板块”，让 Agent 使用用户画像工具保存偏好。

### 会话与启动模式

网页目前是**单活跃会话**，没有新建／切换会话界面；多个浏览器标签页连接同一个会话。底层支持不同会话文件，可在启动时指定：

```powershell
.\venv\Scripts\python.exe main.py --web --session interview
```

会话历史与摘要按 session 保存；用户画像和自选全局共享。同一进程的网页、飞书和定时任务使用当前会话，回复路由独立控制。不要同时运行多个共享这些数据目录的进程。

| 命令参数 | 用途 |
| --- | --- |
| `--web` | 桌面网页；用户创建的定时任务自动调度 |
| `--web --schedule` | 网页，并额外启用旧固定早报和交易时段监控 |
| `--cli` | 命令行聊天 |
| `--mode full` 或不加参数 | CLI、固定时间表和监控 |
| `--report` / `--monitor` | 原有的一次报告／监控入口 |

### 定时任务

“看大盘”右侧的面板支持每日简报或自定义研究。也可以直接说：

- “每个工作日北京时间早上九点生成我的每日简报。”
- “十分钟后分析平安银行的行情和技术指标。”
- “列出我的定时任务，把晨报暂停。”

主 Agent 使用 `create_scheduled_task`、`list_scheduled_tasks`、`update_scheduled_task`、`delete_scheduled_task`；与前端 API 共用任务存储和执行逻辑。

- 时间统一按北京时间，工作日指周一至周五，尚未接入交易所节假日日历。
- 任务跟随创建它的 session，只在程序运行且该会话激活时执行；结果进入聊天记录。
- 定义保存在本地，重启后恢复。错过的用户任务执行一次，不逐日补跑；中断中的模型调用不续跑。
- 每个会话最多 30 条任务。暂停停止后续触发，不中断已运行的分析；删除不删除已经生成的报告。

创建用户任务**不需要 `--schedule`**。该参数额外开启旧时间表：09:00 早报，09:30／13:00 启动监控，11:30／15:00 停止监控；交易时段约每分钟检测异动、每 30 分钟触发分析。使用时避免重复安排相同早报。固定时间表可在 `config.py` 调整。

## 记忆与架构

用户画像按每轮对话注入提示词，完整聊天保存在文件中。超过默认 20000 个**估算 token** 后整理旧轮次，保留最近 4 轮和完整工具配对；摘要与精确位置跨轮保存，原始消息仍供网页回看。摘要失败时保留原历史。

这是文件画像与会话摘要，不是向量检索式记忆，没有引入 Mem0 或数据库。

```text
Web / CLI / 飞书 / 定时任务
            ↓
    文件 Mailbox → 串行主 Agent
                     ├─ 行情、新闻、技术分析工具
                     ├─ 子 Agent + Markdown Skill
                     └─ 用户画像、会话与摘要
```

网页自选、大盘查询可以直接调用工具；需要模型的聊天和任务共用一个执行队列。Skill 位于 `skills/`，是运行文件，不是可随意清理的说明文档。

| 本地路径 | 内容 |
| --- | --- |
| `memory/data.json`、`memory/user.md` | 用户画像与偏好 |
| `memory/sessions/` | 会话原文、摘要和压缩位置 |
| `memory/mailbox/` | 待处理事件 |
| `data_store/watchlist.json` | 自选股 |
| `data_store/scheduled_tasks.json` | 用户定时任务 |
| `data_store/logs/astack.log` | 运行日志，保留旧文件名以兼容已有环境 |

## 飞书与企业微信（可选）

Web 演示无需配置渠道。使用飞书时额外安装：

```powershell
.\venv\Scripts\python.exe -m pip install lark-oapi
```

在飞书开放平台创建并发布机器人应用，配置消息事件 `im.message.receive_v1` 和所需权限，然后在 `.env` 填写 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`。通过 WebSocket 收消息，无需公网回调地址。

企业微信使用 `.env.example` 中的应用凭据、接收端口和验证配置，并需能访问 Webhook。已有 `FEISHU_ALERT_USERS`、`WECOM_ALERT_USERS` 等收件人设置继续用于原有渠道预警。

## 测试与开发

统一检查入口默认使用临时存储、替身模型和替身渠道，不消费真实模型额度或发送消息：

```powershell
.\venv\Scripts\python.exe scripts/check_tools.py --suite all
.\venv\Scripts\python.exe scripts/check_tools.py --suite tools
.\venv\Scripts\python.exe scripts/check_tools.py --suite monitoring
npm --prefix front test
npm --prefix front run build
```

显式检查真实公开数据接口，不调用模型或消息渠道：

```powershell
.\venv\Scripts\python.exe scripts/check_tools.py --live --json data_store/tool-health.json
.\venv\Scripts\python.exe scripts/check_tools.py --live --tool get_hot_stocks
```

真实检查使用有超时的子进程，区分 `PASS`、合法空结果 `EMPTY`、`FAIL`、`TIMEOUT`；失败返回非零退出码。完整模型及渠道联调需要另行手动进行。

离线页面预览：`python tests/preview_web_demo.py`，使用明确标注的样例数据和临时会话；与正式服务共用 8088 端口，先退出其中一个再启动另一个。

前端开发：先启动后端，再执行 `npm --prefix front run dev`，访问 Vite 输出地址。

## 数据与凭据

`.env`、密钥文件、个人笔记、记忆、任务定义、行情缓存、日志、虚拟环境、浏览器验收产物和前端构建目录均不上传。仓库保留 `.env.example` 和前端依赖锁文件；服务默认只监听本机，不开放跨域访问。

## 许可证与致谢

MIT License（沿用项目原有声明）。

- [nanobot](https://github.com/HKUDS/nanobot)：Agent 循环与文件记忆设计参考。
- [AKShare](https://github.com/akfamily/akshare)：财经数据接口。
- React、aiohttp 及其他开源依赖。
