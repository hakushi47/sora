import os
import logging
from datetime import datetime
from typing import List, Dict, Any
from pathlib import Path
from config import Config

logger = logging.getLogger(__name__)

class ObsidianClient:
    def __init__(self):
        self.vault_path = Path(Config.OBSIDIAN_VAULT_PATH)
        self.daily_note_template = Config.OBSIDIAN_DAILY_NOTE_TEMPLATE
        
        # ボルトが存在するかチェック
        if not self.vault_path.exists():
            logger.warning(f"Obsidianボルトが見つかりません: {self.vault_path}")
    
    def create_daily_note(self, messages: List[Dict[str, Any]], date: datetime = None) -> bool:
        """日次ノートを作成してメッセージを記録"""
        if not messages:
            logger.info("Obsidianに記録するメッセージがありません")
            return True
            
        if date is None:
            date = datetime.now()
        
        # ファイル名を生成（例: 2024-01-15.md）
        filename = date.strftime('%Y-%m-%d') + '.md'
        file_path = self.vault_path / filename
        
        # 既存のファイルがある場合は読み込み
        existing_content = ""
        if file_path.exists():
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    existing_content = f.read()
            except Exception as e:
                logger.error(f"既存ファイルの読み込みに失敗: {e}")
        
        # 新しいコンテンツを生成
        new_content = self._format_obsidian_content(messages, date)
        
        # 既存のコンテンツとマージ
        final_content = self._merge_content(existing_content, new_content)
        
        try:
            # ファイルに書き込み
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(final_content)
            
            logger.info(f"Obsidianに日次ノートを作成しました: {file_path}")
            return True
            
        except Exception as e:
            logger.error(f"Obsidianファイルの作成に失敗: {e}")
            return False
    
    def _format_obsidian_content(self, messages: List[Dict[str, Any]], date: datetime) -> str:
        """Obsidian用のコンテンツをフォーマット"""
        # チャンネルごとにグループ化
        channel_groups = {}
        for message in messages:
            channel_id = message['channel_id']
            if channel_id not in channel_groups:
                channel_groups[channel_id] = []
            channel_groups[channel_id].append(message)
        
        content_lines = [
            f"# Slack キーワード収集 - {date.strftime('%Y年%m月%d日')}",
            "",
            f"**収集日時**: {date.strftime('%Y-%m-%d %H:%M:%S')}",
            f"**対象キーワード**: {', '.join(Config.KEYWORDS)}",
            f"**収集件数**: {len(messages)}件",
            "",
            "---",
            ""
        ]
        
        for channel_id, channel_messages in channel_groups.items():
            # チャンネル名を取得（簡易版）
            channel_name = f"チャンネル({channel_id})"
            
            content_lines.append(f"## #{channel_name} ({len(channel_messages)}件)")
            content_lines.append("")
            
            for message in channel_messages:
                # ユーザーIDをそのまま使用（実際の実装ではユーザー名を取得する必要があります）
                username = f"<@{message['user']}>"
                
                # メッセージテキスト
                text = message['text']
                
                # タイムスタンプを日時に変換
                timestamp = datetime.fromtimestamp(float(message['timestamp']))
                time_str = timestamp.strftime('%H:%M')
                
                content_lines.append(f"### {time_str} - {username}")
                content_lines.append("")
                content_lines.append(f"{text}")
                
                if message['permalink']:
                    content_lines.append(f"")
                    content_lines.append(f"[Slackで見る]({message['permalink']})")
                
                content_lines.append("")
                content_lines.append("---")
                content_lines.append("")
        
        return "\n".join(content_lines)

    def append_single_message(self, message: Dict[str, Any], date: datetime = None) -> bool:
        """単一メッセージを当日の日次ノートに追記"""
        try:
            if date is None:
                date = datetime.now()
            filename = date.strftime('%Y-%m-%d') + '.md'
            file_path = self.vault_path / filename
            content = self._format_single_message(message)
            with open(file_path, 'a', encoding='utf-8') as f:
                f.write(content)
            logger.info(f"Obsidianに1件追記しました: {file_path}")
            return True
        except Exception as e:
            logger.error(f"単一メッセージの追記に失敗: {e}")
            return False

    def _format_single_message(self, message: Dict[str, Any]) -> str:
        timestamp = datetime.fromtimestamp(float(message['timestamp']))
        time_str = timestamp.strftime('%H:%M')
        username = message.get('username') or f"<@{message.get('user_id','')}>"
        lines = [
            f"### {time_str} - {username}",
            "",
            f"{message.get('content') or message.get('text','')}",
            "",
            f"[Discordで見る]({message.get('jump_url') or message.get('permalink','')})" if message.get('jump_url') or message.get('permalink') else "",
            "",
            "---",
            "\n"
        ]
        return "\n".join([l for l in lines if l is not None])
    
    def _merge_content(self, existing_content: str, new_content: str) -> str:
        """既存のコンテンツと新しいコンテンツをマージ"""
        if not existing_content.strip():
            return new_content
        
        # 既存のコンテンツに新しいセクションを追加
        # 日付ベースのセクションを探して追加
        lines = existing_content.split('\n')
        
        # 新しいセクションを追加する位置を見つける
        insert_index = len(lines)
        for i, line in enumerate(lines):
            if line.startswith('# ') and 'Slack キーワード収集' in line:
                # 既存のセクションを見つけた場合、その後に挿入
                insert_index = i + 1
                break
        
        # 新しいコンテンツを挿入
        lines.insert(insert_index, "")
        lines.insert(insert_index + 1, new_content)
        
        return '\n'.join(lines)
    
    def create_weekly_summary(self, messages_by_date: Dict[str, List[Dict[str, Any]]]) -> bool:
        """週次サマリーを作成"""
        if not messages_by_date:
            return True
        
        # 今週の月曜日を計算
        today = datetime.now()
        monday = today - timedelta(days=today.weekday())
        
        filename = f"Weekly-Summary-{monday.strftime('%Y-%m-%d')}.md"
        file_path = self.vault_path / filename
        
        content_lines = [
            f"# 週次Slackキーワード収集サマリー",
            f"**期間**: {monday.strftime('%Y年%m月%d日')} - {today.strftime('%Y年%m月%d日')}",
            "",
            f"**対象キーワード**: {', '.join(Config.KEYWORDS)}",
            "",
            "---",
            ""
        ]
        
        total_messages = 0
        for date_str, messages in messages_by_date.items():
            if messages:
                total_messages += len(messages)
                content_lines.append(f"## {date_str} ({len(messages)}件)")
                content_lines.append("")
                
                for message in messages[:5]:  # 各日最大5件まで表示
                    timestamp = datetime.fromtimestamp(float(message['timestamp']))
                    time_str = timestamp.strftime('%H:%M')
                    content_lines.append(f"- **{time_str}**: {message['text'][:100]}...")
                
                if len(messages) > 5:
                    content_lines.append(f"- ...他{len(messages) - 5}件")
                
                content_lines.append("")
        
        content_lines.append(f"**週間合計**: {total_messages}件")
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(content_lines))
            
            logger.info(f"週次サマリーを作成しました: {file_path}")
            return True
            
        except Exception as e:
            logger.error(f"週次サマリーの作成に失敗: {e}")
            return False

