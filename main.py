#!/usr/bin/env python3
"""
Discord Bot Runner
"""

import logging
import sys
from config import Config
from discord_client import SoraBot

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
    setup_logging()
    logger = logging.getLogger(__name__)
    
    try:
        # 設定の検証
        Config.validate()
        logger.info("設定の検証が完了しました")
        
        # Botの起動
        bot = SoraBot()
        bot.run_bot()
            
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