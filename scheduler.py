import schedule
import time
import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any
from discord_client import DiscordMessageCollector
from config import Config

logger = logging.getLogger(__name__)

class MessageScheduler:
    def __init__(self):
        self.discord_collector = DiscordMessageCollector()
        self.schedule_time = Config.SCHEDULE_TIME
        
    def setup_schedule(self):
        """スケジュールを設定"""
        # 毎日指定時刻に実行
        schedule.every().day.at(self.schedule_time).do(self.daily_task)
        
        logger.info(f"スケジュールを設定しました: 毎日 {self.schedule_time}")
    
    def daily_task(self):
        """日次タスクを実行"""
        logger.info("日次タスクを開始します")
        
        try:
            # 非同期タスクを実行
            asyncio.run(self._async_daily_task())
            
        except Exception as e:
            logger.error(f"日次タスクの実行中にエラーが発生しました: {e}")
    
    async def _async_daily_task(self):
        """非同期日次タスク"""
        # ターゲットチャンネルからキーワードなしで全件収集
        messages = await self.discord_collector.collect_all_messages_from_channel_no_keyword(
            channel_id=Config.TARGET_CHANNEL_ID, 
            days_back=1
        )

        # Discordにサマリーを投稿
        post_success = await self.discord_collector.post_summary(messages)
        if not post_success:
            logger.error("Discordでの収集/投稿に失敗しました")

    
    def run_once(self):
        """一度だけ実行（テスト用）"""
        logger.info("一回限りの実行を開始します")
        self.daily_task()
    
    def start_scheduler(self):
        """スケジューラーを開始"""
        self.setup_schedule()
        
        logger.info("スケジューラーを開始しました")
        logger.info("Ctrl+Cで停止できます")
        
        try:
            while True:
                schedule.run_pending()
                time.sleep(60)  # 1分ごとにチェック
        except KeyboardInterrupt:
            logger.info("スケジューラーを停止しました")
        except Exception as e:
            logger.error(f"スケジューラーでエラーが発生しました: {e}")
    
    def get_next_run_time(self):
        """次回実行時刻を取得"""
        next_run = schedule.next_run()
        if next_run:
            return next_run.strftime('%Y-%m-%d %H:%M:%S')
        return "スケジュールが設定されていません"
