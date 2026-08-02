"""
用户记忆系统
借鉴nanobot的memory设计，维护user.md作为长期记忆
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field, asdict

from config import config


@dataclass
class UserProfile:
    """用户画像"""
    name: str = ""
    timezone: str = "Asia/Shanghai"
    language: str = "zh-CN"
    risk_preference: str = "moderate"  # conservative, moderate, aggressive
    investment_style: str = "value"  # value, growth, trend
    investment_horizon: str = "medium"  # short, medium, long


@dataclass
class WatchStock:
    """关注的股票"""
    code: str
    added_at: str
    notes: str = ""
    tags: List[str] = field(default_factory=list)


@dataclass
class UserPreferences:
    """用户偏好"""
    focus_sectors: List[str] = field(default_factory=list)  # 关注的行业
    avoid_sectors: List[str] = field(default_factory=list)  # 回避的行业
    max_position: float = 0.2  # 单票最大仓位
    stop_loss: float = 0.1  # 止损比例
    take_profit: float = 0.2  # 止盈比例
    alert_threshold: float = 3.0  # 异动提醒阈值


class UserMemory:
    """
    用户记忆管理器
    借鉴nanobot的MemoryStore设计
    """

    _instance = None

    def __new__(cls):
        """单例模式"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self.memory_dir = config.MEMORY_DIR
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        self.user_file = self.memory_dir / "user.md"
        # history.md 现在只记录关键事件摘要，不记录完整对话
        # 完整对话历史保存在 sessions/ 目录中
        self.history_file = self.memory_dir / "history.md"
        self.data_file = self.memory_dir / "data.json"

        # 加载数据
        self.profile = UserProfile()
        self.preferences = UserPreferences()
        self.conversation_history: List[Dict[str, Any]] = []

        self._load()

    def _load(self):
        """加载用户数据"""
        # 从JSON加载结构化数据
        if self.data_file.exists():
            try:
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.profile = UserProfile(**data.get('profile', {}))
                    self.preferences = UserPreferences(**data.get('preferences', {}))
            except Exception as e:
                print(f"加载用户数据失败: {e}")

        # 从user.md加载补充信息
        if self.user_file.exists():
            self._parse_user_md()

    def _parse_user_md(self):
        """解析user.md文件"""
        try:
            content = self.user_file.read_text(encoding='utf-8')

            # 解析关注领域
            if '关注领域' in content or '## Interests' in content:
                # 提取行业关注
                sectors_match = re.search(r'(?:关注领域|Interests).*?\n(.*?)(?:\n##|\Z)', content, re.DOTALL)
                if sectors_match:
                    sectors_text = sectors_match.group(1)
                    # 解析列表项
                    sectors = re.findall(r'[-*]\s*([^\n]+)', sectors_text)
                    self.preferences.focus_sectors = [s.strip() for s in sectors if s.strip()]

            # 解析风险偏好
            risk_match = re.search(r'(?:风险偏好|Risk).*?(保守|稳健|积极|conservative|moderate|aggressive)', content, re.IGNORECASE)
            if risk_match:
                risk_map = {
                    '保守': 'conservative', '稳健': 'moderate', '积极': 'aggressive',
                    'conservative': 'conservative', 'moderate': 'moderate', 'aggressive': 'aggressive'
                }
                self.profile.risk_preference = risk_map.get(risk_match.group(1).lower(), 'moderate')

            # 解析投资风格
            style_match = re.search(r'(?:投资风格|Style).*?(价值|成长|趋势|value|growth|trend)', content, re.IGNORECASE)
            if style_match:
                style_map = {
                    '价值': 'value', '成长': 'growth', '趋势': 'trend',
                    'value': 'value', 'growth': 'growth', 'trend': 'trend'
                }
                self.profile.investment_style = style_map.get(style_match.group(1).lower(), 'value')

        except Exception as e:
            print(f"解析user.md失败: {e}")

    def save(self):
        """保存用户数据"""
        try:
            # 保存结构化数据
            data = {
                'profile': asdict(self.profile),
                'preferences': asdict(self.preferences),
                'updated_at': datetime.now().isoformat()
            }

            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            # 保存user.md（供人阅读）
            self._write_user_md()

        except Exception as e:
            print(f"保存用户数据失败: {e}")

    def _write_user_md(self):
        """写入user.md"""
        content = f"""# 用户画像 (User Profile)

> 此文件由系统自动维护，也可手动编辑
> 最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}

## 基本信息 (Basic Info)

- **姓名**: {self.profile.name or '未设置'}
- **时区**: {self.profile.timezone}
- **语言**: {self.profile.language}

## 投资偏好 (Investment Preferences)

### 风险承受
- **风险偏好**: {self.profile.risk_preference} (conservative/moderate/aggressive)
- **投资风格**: {self.profile.investment_style} (value/growth/trend)
- **投资周期**: {self.profile.investment_horizon} (short/medium/long)

### 关注领域
{self._format_list(self.preferences.focus_sectors) or '- 未设置'}

### 回避领域
{self._format_list(self.preferences.avoid_sectors) or '- 未设置'}

### 交易规则
- **单票最大仓位**: {self.preferences.max_position * 100}%
- **止损线**: {self.preferences.stop_loss * 100}%
- **止盈线**: {self.preferences.take_profit * 100}%
- **异动提醒阈值**: {self.preferences.alert_threshold}%

## 会话存储 (Session Storage)

**完整对话历史**已保存在 `memory/sessions/` 目录下的会话文件中，不在此文件记录。

**关键事件摘要**记录在 `memory/history.md` 文件中。

## 对话历史摘要 (Conversation Summary)

_重要分析结论和事件将在此汇总_

"""

        self.user_file.write_text(content, encoding='utf-8')

    def _format_list(self, items: List[str]) -> str:
        """格式化列表"""
        if not items:
            return ""
        return '\n'.join(f'- {item}' for item in items)

    def get_context(self) -> str:
        """获取用户上下文（用于LLM提示词）"""
        context_parts = [
            "## 用户画像",
            f"- 风险偏好: {self.profile.risk_preference}",
            f"- 投资风格: {self.profile.investment_style}",
            f"- 投资周期: {self.profile.investment_horizon}",
        ]

        if self.preferences.focus_sectors:
            context_parts.append(f"- 关注领域: {', '.join(self.preferences.focus_sectors)}")

        if self.preferences.avoid_sectors:
            context_parts.append(f"- 回避领域: {', '.join(self.preferences.avoid_sectors)}")

        context_parts.extend([
            f"- 止损线: {self.preferences.stop_loss * 100}%",
            f"- 止盈线: {self.preferences.take_profit * 100}%",
        ])

        return '\n'.join(context_parts)

    def update_profile(self, **kwargs):
        """更新用户画像"""
        for key, value in kwargs.items():
            if hasattr(self.profile, key):
                setattr(self.profile, key, value)
        self.save()

    def update_preferences(self, **kwargs):
        """更新用户偏好"""
        for key, value in kwargs.items():
            if hasattr(self.preferences, key):
                setattr(self.preferences, key, value)
        self.save()

    def add_focus_sector(self, sector: str):
        """添加关注领域"""
        if sector not in self.preferences.focus_sectors:
            self.preferences.focus_sectors.append(sector)
            self.save()

    def remove_focus_sector(self, sector: str):
        """移除关注领域"""
        if sector in self.preferences.focus_sectors:
            self.preferences.focus_sectors.remove(sector)
            self.save()

    def append_history(self, entry: str, event_type: str = "general"):
        """
        添加关键事件摘要到 history.md

        关键事件类型：
        - user_profile: 用户画像更新（风险偏好、关注领域等变更）
        - analysis_conclusion: 重要分析结论（如"决定买入某股票"、"发现重大风险"）
        - alert: 异常告警（股票异动、监控触发）
        - weekly_summary: 每周定期总结
        - system: 系统事件（初始化、清除历史、配置变更等）
        - command: 用户命令（添加自选股、生成报告等）

        不记录的内容：
        - 普通查询对话（"查一下茅台行情"）
        - 日常技术分析请求
        - 简单问答

        完整对话历史已保存在 sessions/ 目录下的会话文件中。

        Args:
            entry: 事件描述摘要
            event_type: 事件类型标记
        """
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
        line = f"[{timestamp}] [{event_type}] {entry}\n"

        with open(self.history_file, 'a', encoding='utf-8') as f:
            f.write(line)

    def get_recent_history(self, lines: int = 20) -> str:
        """获取最近历史"""
        if not self.history_file.exists():
            return ""

        try:
            with open(self.history_file, 'r', encoding='utf-8') as f:
                all_lines = f.readlines()
                return ''.join(all_lines[-lines:])
        except Exception:
            return ""

    def needs_initialization(self) -> bool:
        """检查是否需要初始化用户画像

        如果关键字段都为空或默认值，则认为需要初始化
        """
        # 检查基本信息是否已设置
        if not self.profile.name:
            return True

        # 检查user.md文件是否存在且有实质内容
        if not self.user_file.exists():
            return True

        try:
            content = self.user_file.read_text(encoding='utf-8')
            # 检查是否有未设置的标记
            if '未设置' in content or '未填写' in content:
                return True
            # 检查是否是默认模板（通过检查最后更新时间）
            if '2024-01-01 00:00' in content:
                return True
        except Exception:
            return True

        return False

    def get_profile_summary(self) -> Dict[str, Any]:
        """获取当前用户画像摘要，用于显示"""
        return {
            "name": self.profile.name or "未设置",
            "risk_preference": self.profile.risk_preference,
            "investment_style": self.profile.investment_style,
            "investment_horizon": self.profile.investment_horizon,
            "focus_sectors": self.preferences.focus_sectors,
            "avoid_sectors": self.preferences.avoid_sectors,
            "max_position": f"{self.preferences.max_position * 100}%",
            "stop_loss": f"{self.preferences.stop_loss * 100}%",
            "take_profit": f"{self.preferences.take_profit * 100}%",
        }


# 全局实例
user_memory = UserMemory()
