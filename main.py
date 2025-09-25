#!/usr/bin/env python3
"""
Discord キーワード収集Bot
特定のキーワードを含むメッセージを収集し、毎日定時にDiscordに投稿する
"""

import logging
import argparse
import sys
from datetime import datetime
from config import Config
from scheduler import MessageScheduler

# ログ設定
def setup_logging(level=logging.INFO):
    """ログ設定を初期化"""
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('discord_bot.log', encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )

def main():
    """メイン関数"""
    parser = argparse.ArgumentParser(description='Discord キーワード収集Bot')
    parser.add_argument('--once', action='store_true', help='一回だけ実行（テスト用）')
    parser.add_argument('--debug', action='store_true', help='デバッグモードで実行')
    parser.add_argument('--schedule', action='store_true', help='スケジューラーを開始')
    parser.add_argument('--monitor', action='store_true', help='常時監視モードで起動（キーワード検出→リアクション）')
    
    args = parser.parse_args()
    
    # ログレベル設定
    log_level = logging.DEBUG if args.debug else logging.INFO
    setup_logging(log_level)
    
    logger = logging.getLogger(__name__)
    
    try:
        # 設定の検証
        Config.validate()
        logger.info("設定の検証が完了しました")
        
        # スケジューラー/監視の初期化
        scheduler = MessageScheduler()
        
        if args.once:
            # 一回だけ実行
            logger.info("一回限りの実行モード")
            scheduler.run_once()
            
        elif args.schedule:
            # スケジューラーを開始
            logger.info("スケジューラーモード")
            scheduler.start_scheduler()
        
        elif args.monitor:
            # 常時監視モード
            logger.info("常時監視モード")
            from discord_client import DiscordMessageCollector
            import asyncio
            collector = DiscordMessageCollector()
            asyncio.run(collector.start_monitor())
            
        else:
            # デフォルトはスケジューラーモード
            logger.info("デフォルトモード: スケジューラーを開始")
            scheduler.start_scheduler()
            
    except ValueError as e:
        logger.error(f"設定エラー: {e}")
        logger.error("環境変数ファイル(.env)を確認してください")
        sys.exit(1)
        
    except KeyboardInterrupt:
        logger.info("プログラムを終了します")
        sys.exit(0)
        
    except Exception as e:
        logger.error(f"予期しないエラーが発生しました: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
