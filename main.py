#!/usr/bin/env python3
"""
Discord Bot Runner
"""

import logging
import sys
import argparse
from config import Config
from discord_client import SoraBot

# ログ設定
def setup_logging(debug=False):
    """ログ設定を初期化"""
    level = logging.DEBUG if debug else logging.INFO
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
    parser = argparse.ArgumentParser(description="Sora Bot Runner")
    parser.add_argument("--monitor", action="store_true", help="Run the bot in persistent monitoring mode.")
    parser.add_argument("--schedule", action="store_true", help="Run the bot in scheduled (persistent) mode.")
    parser.add_argument("--once", action="store_true", help="Run the daily task once and exit.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    args = parser.parse_args()

    setup_logging(args.debug)
    logger = logging.getLogger(__name__)
    
    try:
        Config.validate()
        logger.info("設定の検証が完了しました")
        
        bot = SoraBot()

        if args.monitor or args.schedule:
            logger.info("常時監視モードで起動します...")
            bot.run_bot()
        elif args.once:
            logger.info("一回限りのタスクを実行します...")
            bot.run_once_collect_and_post()
        else:
            logger.info("起動モードが指定されていません。常時監視モードで起動します...")
            bot.run_bot()
            
    except KeyboardInterrupt:
        logger.info("プログラムを終了します")
        sys.exit(0)
    except Exception as e:
        logger.error(f"予期しないエラーが発生しました: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()