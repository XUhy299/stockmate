"""
任务调度模块
管理定时任务：早间报告、交易日判断

注意：实时监控功能已移至 MonitoringService
"""
import asyncio
import logging
from typing import List
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import schedule

from config import config
from services.monitoring_service import monitoring_service
from services.morning_report_service import morning_report_service

logger = logging.getLogger(__name__)


class TaskScheduler:
    """
    任务调度器
    管理定时任务，实时监控由 MonitoringService 独立处理
    """

    def __init__(self, agent):
        self.agent = agent
        self.running = False
        self.tasks: List[asyncio.Task] = []
        self.cn_tz = ZoneInfo("Asia/Shanghai")

        # 设置监控服务的Agent引用
        monitoring_service.set_agent(agent)

    def is_trading_time(self) -> bool:
        """判断当前是否在交易时间"""
        now = datetime.now(self.cn_tz)

        # 周末不开市
        if now.weekday() >= 5:
            return False

        current_time = now.time()

        # 上午 9:30 - 11:30
        morning_start = time(9, 30)
        morning_end = time(11, 30)

        # 下午 13:00 - 15:00
        afternoon_start = time(13, 0)
        afternoon_end = time(15, 0)

        return (
            (morning_start <= current_time <= morning_end) or
            (afternoon_start <= current_time <= afternoon_end)
        )

    def is_trading_day(self) -> bool:
        """判断今天是否交易日"""
        now = datetime.now(self.cn_tz)
        return now.weekday() < 5

    async def start(self):
        """启动调度器"""
        if self.running:
            return

        self.running = True
        logger.info("🚀 任务调度器启动")

        # 设置定时任务
        self._setup_schedules()

        # 启动调度循环
        self.tasks.append(asyncio.create_task(self._schedule_loop()))

        # 如果当前是交易时间，自动启动监控
        if self.is_trading_time():
            logger.info("📡 当前为交易时间，自动启动监控服务")
            await monitoring_service.start()

    def _setup_schedules(self):
        """设置定时任务"""
        # 早间报告 - 每天9:00（交易日）
        schedule.every().day.at(config.MORNING_REPORT_TIME).do(
            lambda: asyncio.create_task(self._generate_morning_report())
        )

        # 开市监控启动 - 上午9:30
        schedule.every().day.at(config.AM_START).do(
            lambda: asyncio.create_task(self._start_market_monitoring())
        )

        # 午休暂停监控 - 上午11:30
        schedule.every().day.at(config.AM_END).do(
            lambda: asyncio.create_task(self._pause_market_monitoring())
        )

        # 下午监控启动 - 下午13:00
        schedule.every().day.at(config.PM_START).do(
            lambda: asyncio.create_task(self._start_market_monitoring())
        )

        # 收市停止监控 - 下午15:00
        schedule.every().day.at(config.PM_END).do(
            lambda: asyncio.create_task(self._stop_market_monitoring())
        )

        logger.info(f"📅 定时任务已设置:")
        logger.info(f"   - 早间报告: {config.MORNING_REPORT_TIME}")
        logger.info(f"   - 上午监控: {config.AM_START} - {config.AM_END}")
        logger.info(f"   - 下午监控: {config.PM_START} - {config.PM_END}")

    async def _schedule_loop(self):
        """调度循环 - 每秒检查定时任务"""
        while self.running:
            try:
                # 运行待执行的定时任务
                schedule.run_pending()
                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"调度循环出错: {e}")
                await asyncio.sleep(5)

    async def _generate_morning_report(self):
        """生成早间报告 - 发送事件唤醒主Agent"""
        if not self.is_trading_day():
            logger.info("今天非交易日，跳过早间报告")
            return

        logger.info("📰 触发早间报告生成...")

        report_date = datetime.now(self.cn_tz).strftime('%Y-%m-%d')

        content = f"""【早间报告任务】

请生成A股每日市场分析早报，日期：{report_date}。

报告要求：
1. **市场情绪**：获取并分析当前市场情绪（涨跌家数、涨跌停数量）
2. **自选股分析**：获取用户的自选列表，分析每只股票的行情和技术指标
3. **热门股票调研**：
   - 获取成交额排行前10的股票
   - 获取涨幅排行前10的股票（筛选涨幅2%-7%的）
   - 分析其中值得关注的股票
4. **板块资金流向**：获取板块资金流入流出情况
5. **投资建议**：基于以上分析给出今日操作建议

请使用 morning_report Skill 指导报告生成。"""

        # 发送事件唤醒主Agent
        if self.agent and hasattr(self.agent, 'post_event'):
            try:
                await self.agent.post_event(
                    "morning_report",
                    content,
                    metadata={"date": report_date, "type": "morning_report"}
                )
                logger.info("✅ 早间报告事件已发送到主Agent")
            except Exception as e:
                logger.error(f"发送早间报告事件失败: {e}")
        else:
            logger.warning("主Agent未设置，无法触发生成早间报告")

    async def _start_market_monitoring(self):
        """启动市场监控"""
        if not self.is_trading_day():
            return
        logger.info("📈 开市，启动实时监控")
        await monitoring_service.start()

    async def _pause_market_monitoring(self):
        """暂停市场监控（午休）"""
        logger.info("⏸️ 午休，暂停实时监控")
        monitoring_service.stop()

    async def _stop_market_monitoring(self):
        """停止市场监控（收市）"""
        logger.info("📉 收市，停止实时监控")
        monitoring_service.stop()

        # 收市后发送监控总结
        await self._send_daily_summary()

    async def _send_daily_summary(self):
        """发送每日监控总结"""
        try:
            status = monitoring_service.get_status()
            alert_count = status.get("alert_count", 0)

            from core.notifier import notification_manager
            await notification_manager.send_report(
                title=f"📊 今日监控总结 {datetime.now().strftime('%Y-%m-%d')}",
                content=f"""
今日监控统计：
- 监控股票数: {status.get('watchlist_count', 0)}
- 预警次数: {alert_count}
- 定期分析次数: {len([s for s in status.get('stocks', {}).values() if s.get('last_routine_analysis')])}

详细记录请查看 history.md
"""
            )
        except Exception as e:
            logger.error(f"发送每日总结失败: {e}")

    async def stop(self):
        """停止调度器"""
        self.running = False

        # 停止监控服务
        monitoring_service.stop()

        # 取消所有任务
        for task in self.tasks:
            task.cancel()

        await asyncio.gather(*self.tasks, return_exceptions=True)
        logger.info("🛑 任务调度器已停止")
