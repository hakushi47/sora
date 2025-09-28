import os
from dotenv import load_dotenv

# 環境変数を読み込み
load_dotenv()

class Config:
    # Discord設定
    DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
    TARGET_CHANNEL_IDS = [int(cid.strip()) for cid in os.getenv('TARGET_CHANNEL_IDS', '0').split(',')]
    GUILD_ID = int(os.getenv('GUILD_ID', '0')) if os.getenv('GUILD_ID') else None


    
    # スケジュール設定
    SCHEDULE_TIME = os.getenv('SCHEDULE_TIME', '09:00')

    # キーワードとリアクションのマッピング
    KEYWORD_REACTIONS = os.getenv('KEYWORD_REACTIONS', 'なう:🕒,わず:✅,うぃる:🗓️')

    # データベース設定
    DATABASE_URL = os.getenv('DATABASE_URL')
    
    @classmethod
    def validate(cls):
        """設定値の検証"""
        required_vars = ['DISCORD_BOT_TOKEN']
        missing_vars = [var for var in required_vars if not getattr(cls, var)]
        
        if missing_vars:
            raise ValueError(f"以下の環境変数が設定されていません: {', '.join(missing_vars)}")
        
        if cls.TARGET_CHANNEL_ID == 0:
            raise ValueError("TARGET_CHANNEL_IDが設定されていません")
        
        return True
