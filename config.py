"""
A股分析Agent系统配置
"""
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional

# 尝试加载环境变量
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # 如果未安装dotenv，使用系统环境变量

# 项目根目录
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data_store"
MEMORY_DIR = BASE_DIR / "memory"

# 确保目录存在
DATA_DIR.mkdir(parents=True, exist_ok=True)
MEMORY_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Config:
    """系统配置"""

    # === LLM配置 ===
    OPENAI_API_KEY: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    OPENAI_API_BASE: str = field(default_factory=lambda: os.getenv("OPENAI_API_BASE", ""))
    OPENAI_MODEL: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o"))
    # 注意: Moonshot kimi-k2.5 只支持 temperature=1
    OPENAI_TEMPERATURE: float = field(default_factory=lambda: float(os.getenv("OPENAI_TEMPERATURE", "0.7")))

    # === 股票监控配置 ===
    # 监控间隔（秒），开市期间每30秒检查一次
    MONITOR_INTERVAL: int = 30

    # 异常波动阈值
    PRICE_CHANGE_THRESHOLD: float = 3.0  # 价格变动百分比阈值（%）
    VOLUME_SURGE_THRESHOLD: float = 200.0  # 成交量突增阈值（相对前一日%）
    RSI_OVERBOUGHT: float = 70.0  # RSI超买阈值
    RSI_OVERSOLD: float = 30.0  # RSI超卖阈值

    # 技术指标阈值
    MACD_SIGNAL_THRESHOLD: float = 0.5  # MACD信号阈值
    BOLL_BREAKOUT_THRESHOLD: float = 1.0  # 布林带突破阈值

    # === 定时任务配置 ===
    # 早间报告时间（开市前）
    MORNING_REPORT_TIME: str = "09:00"  # 每天上午9点生成报告

    # 交易时间
    AM_START: str = "09:30"
    AM_END: str = "11:30"
    PM_START: str = "13:00"
    PM_END: str = "15:00"

    # === 监控配置 ===
    # 监控间隔（秒）
    MONITOR_INTERVAL: int = 60  # 每分钟检查一次

    # 定期分析间隔（秒）- 无异常时触发
    ROUTINE_ANALYSIS_INTERVAL: int = 30 * 60  # 30分钟

    # 预警冷却期（秒）- 同一股票多次预警间隔
    ALERT_COOLDOWN: int = 180  # 3分钟

    # 异常阈值
    PRICE_CHANGE_THRESHOLD: float = 3.0  # 涨跌幅阈值(%)
    VOLUME_RATIO_THRESHOLD: float = 2.5  # 成交量倍率阈值
    TICK_DELTA_THRESHOLD: float = 0.8  # 短时变动阈值(%)

    # === 数据存储配置 ===
    DATA_DIR: Path = field(default_factory=lambda: BASE_DIR / "data_store")
    MEMORY_DIR: Path = field(default_factory=lambda: BASE_DIR / "memory")
    WATCHLIST_FILE: Path = field(default=None)
    ALERT_HISTORY_FILE: Path = field(default=None)
    DAILY_REPORT_DIR: Path = field(default=None)
    MARKET_DATA_CACHE: Path = field(default=None)

    # === 通知配置 ===
    NOTIFICATION_ENABLED: bool = True
    NOTIFICATION_CHANNELS: List[str] = field(default_factory=lambda: ["console"])

    # 钉钉通知（可选）
    DINGTALK_WEBHOOK: str = ""
    DINGTALK_SECRET: str = ""

    # 企业微信通知（可选，用于Webhook推送）
    WECOM_WEBHOOK: str = ""

    # 企业微信应用配置（用于API发送消息）
    WECOM_CORP_ID: str = field(default_factory=lambda: os.getenv("WECOM_CORP_ID", ""))
    WECOM_CORP_SECRET: str = field(default_factory=lambda: os.getenv("WECOM_CORP_SECRET", ""))
    WECOM_AGENT_ID: str = field(default_factory=lambda: os.getenv("WECOM_AGENT_ID", ""))
    WECOM_WEBHOOK_PORT: int = field(default_factory=lambda: int(os.getenv("WECOM_WEBHOOK_PORT", "8080")))
    WECOM_TOKEN: str = field(default_factory=lambda: os.getenv("WECOM_TOKEN", ""))
    WECOM_ENCODING_AES_KEY: str = field(default_factory=lambda: os.getenv("WECOM_ENCODING_AES_KEY", ""))

    # 飞书配置（用于双向通信）
    FEISHU_APP_ID: str = field(default_factory=lambda: os.getenv("FEISHU_APP_ID", ""))
    FEISHU_APP_SECRET: str = field(default_factory=lambda: os.getenv("FEISHU_APP_SECRET", ""))
    FEISHU_ENCRYPT_KEY: str = field(default_factory=lambda: os.getenv("FEISHU_ENCRYPT_KEY", ""))
    FEISHU_VERIFICATION_TOKEN: str = field(default_factory=lambda: os.getenv("FEISHU_VERIFICATION_TOKEN", ""))

    # === AKShare配置 ===
    AKSHARE_TIMEOUT: int = 30  # 请求超时（秒）
    AKSHARE_RETRY_TIMES: int = 3  # 重试次数

    # === 分析配置 ===
    # 热门股票数量
    HOT_STOCKS_COUNT: int = 20

    # 新闻分析数量
    NEWS_ANALYSIS_COUNT: int = 10

    # K线数据天数
    KLINE_DAYS: int = 60

    # === 上下文压缩配置 ===
    # Token阈值，超过此值进行上下文压缩
    CONTEXT_COMPRESS_THRESHOLD: int = 200000

    # === Agent Idle/挂机配置 ===
    # Idle状态检查间隔（秒）- 检查是否有新事件
    AGENT_IDLE_CHECK_INTERVAL: int = 5

    # Idle状态最大持续时间（秒）- 超过此时间可执行定期任务，0表示无限
    AGENT_IDLE_MAX_DURATION: int = 0

    # WORK PHASE最大迭代次数 - 超过后强制进入idle
    AGENT_MAX_WORK_ITERATIONS: int = 50

    # === Agent工具/Skill权限配置 ===
    # 主Agent可用工具列表（空列表表示全部可用）
    MAIN_AGENT_TOOLS: List[str] = field(default_factory=list)

    # 子Agent可用工具列表（空列表表示全部可用）
    SUBAGENT_TOOLS: List[str] = field(default_factory=lambda: [
        "load_skill",
        "get_stock_quote", "get_kline", "get_stock_news", "get_hot_stocks",
        "get_market_sentiment", "get_watchlist",
        "calculate_indicators", "detect_anomalies", "analyze_stock", "screen_stocks",
    ])

    # 主Agent可用Skill列表（用于加载skill内容指导回答）
    MAIN_AGENT_SKILLS: List[str] = field(default_factory=lambda: [
    ])

    # 子Agent可用Skill列表
    SUBAGENT_SKILLS: List[str] = field(default_factory=lambda: [
        "research_report",
        "morning_report",
        "alert_analysis",
    ])

    # === 日志配置 ===
    LOG_LEVEL: str = "INFO"
    LOG_FILE: Optional[Path] = field(default=None)  # 在__post_init__中设置

    def __post_init__(self):
        """初始化后创建必要目录并设置路径"""
        # 设置默认路径
        if self.WATCHLIST_FILE is None:
            self.WATCHLIST_FILE = self.DATA_DIR / "watchlist.json"
        if self.ALERT_HISTORY_FILE is None:
            self.ALERT_HISTORY_FILE = self.DATA_DIR / "alert_history.json"
        if self.DAILY_REPORT_DIR is None:
            self.DAILY_REPORT_DIR = self.DATA_DIR / "daily_reports"
        if self.MARKET_DATA_CACHE is None:
            self.MARKET_DATA_CACHE = self.DATA_DIR / "market_cache.json"
        if self.LOG_FILE is None:
            self.LOG_FILE = self.DATA_DIR / "logs" / "astack.log"

        # 创建目录
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        if self.LOG_FILE:
            self.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.DAILY_REPORT_DIR.mkdir(parents=True, exist_ok=True)


# 全局配置实例
config = Config()
