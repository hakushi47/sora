try:
    import audioop
except ImportError:
    import sys
    # Create a dummy audioop module
    class DummyAudioop:
        def __getattr__(self, name):
            # Return a dummy function or value for any attribute accessed
            return lambda *args, **kwargs: None
    sys.modules['audioop'] = DummyAudioop()

import discord
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from config import Config

logger = logging.getLogger(__name__)

class DiscordMessageCollector:
    def __init__(self):
        self.bot_token = Config.DISCORD_BOT_TOKEN
        self.target_channel_id = Config.TARGET_CHANNEL_ID
        self.intents = discord.Intents.none() # Start with no intents
        self.intents.message_content = True
        self.intents.guilds = True
        self.intents.messages = True
        self.client = discord.Client(intents=self.intents)
        
    async def collect_messages_from_channel(self, channel_id: int, days_back: int = 1) -> List[Dict[str, Any]]:
        """指定されたチャンネルからメッセージを収集"""
        collected_messages = []
        
        try:
            channel = self.client.get_channel(channel_id)
            if not channel:
                logger.error(f"チャンネル {channel_id} が見つかりません")
                return collected_messages
            
            # 指定日数前からのメッセージを取得
            after_date = datetime.now() - timedelta(days=days_back)
            
            async for message in channel.history(after=after_date, limit=1000):
                collected_messages.append({
                    'channel_id': channel_id,
                    'channel_name': channel.name,
                    'user_id': message.author.id,
                    'username': message.author.display_name or message.author.name,
                    'content': message.content,
                    'timestamp': message.created_at.timestamp(),
                    'message_id': message.id,
                    'jump_url': message.jump_url,
                    'guild_id': message.guild.id if message.guild else None,
                    'guild_name': message.guild.name if message.guild else None
                })
                    
        except Exception as e:
            logger.error(f"チャンネル {channel_id} からのメッセージ取得に失敗: {e}")
            
        return collected_messages
    
    async def collect_all_messages(self, guild_id: int = None, days_back: int = 1) -> List[Dict[str, Any]]:
        """指定チャンネルからメッセージを収集"""
        all_messages = []
        
        try:
            # 指定チャンネルからのみメッセージを収集
            logger.info(f"チャンネル '{self.target_channel_id}' からメッセージを収集中...")
            messages = await self.collect_messages_from_channel(self.target_channel_id, days_back)
            all_messages.extend(messages)
            
            logger.info(f"チャンネル '{self.target_channel_id}' から {len(messages)} 件のメッセージを収集")
        
        except Exception as e:
            logger.error(f"メッセージ収集中にエラーが発生: {e}")
            
        return all_messages

    async def collect_messages_from_user_for_day(self, user_id: int, channel_id: int) -> List[Dict[str, Any]]:
        """指定されたユーザーのその日のメッセージを収集"""
        collected_messages = []
        
        try:
            channel = self.client.get_channel(channel_id)
            if not channel:
                logger.error(f"チャンネル {channel_id} が見つかりません")
                return collected_messages
            
            today = datetime.now().date()
            
            async for message in channel.history(limit=1000): # Limit to 1000 messages for performance
                message_date = message.created_at.date()
                if message_date == today and message.author.id == user_id:
                    collected_messages.append({
                        'channel_id': channel_id,
                        'channel_name': channel.name,
                        'user_id': message.author.id,
                        'username': message.author.display_name or message.author.name,
                        'content': message.content,
                        'timestamp': message.created_at.timestamp(),
                        'message_id': message.id,
                        'jump_url': message.jump_url,
                        'guild_id': message.guild.id if message.guild else None,
                        'guild_name': message.guild.name if message.guild else None
                    })
                # メッセージが今日の分より古くなったら終了
                if message_date < today:
                    break
                    
        except Exception as e:
            logger.error(f"ユーザー {user_id} のメッセージ収集に失敗: {e}")
            
        return collected_messages

    async def post_summary(self, messages: List[Dict[str, Any]], channel_id: int = None) -> bool:
        """収集したメッセージのサマリーをDiscordに投稿"""
        if not messages:
            logger.info("投稿するメッセージがありません")
            return True
            
        target_channel_id = channel_id or self.target_channel_id
        
        try:
            channel = self.client.get_channel(target_channel_id)
            if not channel:
                logger.error(f"投稿先チャンネル {target_channel_id} が見つかりません")
                return False
            
            # メッセージをフォーマット
            summary_embed = self._format_summary_embed(messages)
            
            await channel.send(embed=summary_embed)
            logger.info(f"サマリーを #{channel.name} に投稿しました")
            return True
                
        except Exception as e:
            logger.error(f"サマリーの投稿に失敗: {e}")
            return False
    
    def _format_summary_embed(self, messages: List[Dict[str, Any]]) -> discord.Embed:
        """メッセージサマリーをDiscord Embedでフォーマット"""
        if not messages:
            embed = discord.Embed(
                title="📝 収集サマリー",
                description="メッセージは見つかりませんでした。",
                color=0x00ff00,
                timestamp=datetime.now()
            )
            return embed
        
        # チャンネルごとにグループ化
        channel_groups = {}
        for message in messages:
            channel_name = message['channel_name']
            if channel_name not in channel_groups:
                channel_groups[channel_name] = []
            channel_groups[channel_name].append(message)
        
        embed = discord.Embed(
            title="📝 収集サマリー",
            description=f"**収集件数**: {len(messages)}件",
            color=0x00ff00,
            timestamp=datetime.now()
        )
        
        # 各チャンネルのメッセージを追加
        for channel_name, channel_messages in channel_groups.items():
            field_value = ""
            for message in channel_messages[:10]:  # 各チャンネル最大10件まで表示
                timestamp = datetime.fromtimestamp(message['timestamp'])
                time_str = timestamp.strftime('%H:%M')
                
                # メッセージ内容を短縮
                content = message['content']
                if len(content) > 100:
                    content = content[:100] + "..."
                
                field_value += f"**{time_str}** {message['username']}: {content}\n"
            
            if len(channel_messages) > 10:
                field_value += f"...他{len(channel_messages) - 10}件"
            
            if field_value:
                embed.add_field(
                    name=f"#{channel_name} ({len(channel_messages)}件)",
                    value=field_value,
                    inline=False
                )
        
        return embed
    
    async def start_bot(self):
        """Discord Botを開始（常駐）"""
        @self.client.event
        async def on_ready():
            logger.info(f'{self.client.user} としてログインしました')
            logger.info(f'接続中のギルド数: {len(self.client.guilds)}')
        
        try:
            await self.client.start(self.bot_token)
        except Exception as e:
            logger.error(f"Discord Botの開始に失敗: {e}")
    
    async def close(self):
        """Botを終了"""
        await self.client.close()



    async def run_once_collect_and_post(self, days_back: int = 1) -> bool:
        """ログイン→収集→投稿→終了までを一度で実行"""
        done_flag = {
            'ran': False,
            'success': True,
        }

        @self.client.event
        async def on_ready():
            logger.info(f'{self.client.user} としてログインしました')
            logger.info(f'接続中のギルド数: {len(self.client.guilds)}')
            try:
                messages = await self.collect_all_messages(guild_id=Config.GUILD_ID, days_back=days_back)
                if not messages:
                    logger.info("収集されたメッセージがありません")
                else:
                    logger.info(f"{len(messages)}件のメッセージを収集しました")
                    await self.post_summary(messages)
                done_flag['ran'] = True
            except Exception as e:
                done_flag['success'] = False
                logger.error(f"収集/投稿処理でエラー: {e}")
            finally:
                await self.close()

        try:
            await self.client.start(self.bot_token)
        except Exception as e:
            logger.error(f"Discord Botの開始に失敗: {e}")
            return False
        
        return done_flag['success'] and done_flag['ran']





    async def start_monitor(self):
        """常時監視モードを開始。メッセージにリアクション付与"""

        @self.client.event
        async def on_ready():
            logger.info(f'{self.client.user} として監視を開始')

        @self.client.event
        async def on_message(message: discord.Message):
            try:
                # 自分のメッセージはスキップ
                if message.author.id == self.client.user.id:
                    return
                if not message.guild:
                    return
                # 指定チャンネル以外のメッセージはスキップ
                if message.channel.id != self.target_channel_id:
                    return

                # Botへのメンションがあるかチェック
                if self.client.user in message.mentions:
                    # メンションされたユーザーのまとめリクエストを処理
                    mentioned_users = [user for user in message.mentions if user != self.client.user]
                    if mentioned_users:
                        target_user = mentioned_users[0] # 最初のユーザーを対象とする
                        logger.info(f"Botとユーザー {target_user.display_name} へのメンションを検出")

                        # メンションされたユーザーのその日のメッセージを収集
                        user_messages = await self.collect_messages_from_user_for_day(
                            user_id=target_user.id,
                            channel_id=message.channel.id
                        )

                        # サマリーを投稿
                        if user_messages:
                            summary_embed = self._format_summary_embed(user_messages)
                            await message.channel.send(f"{target_user.display_name} さんの今日のまとめです:", embed=summary_embed)
                            logger.info(f"{target_user.display_name} さんのサマリーを投稿しました")
                        else:
                            await message.channel.send(f"{target_user.display_name} さんの今日のメッセージは見つかりませんでした。")
                            logger.info(f"{target_user.display_name} さんのメッセージは見つかりませんでした。")
                        return # メンション処理が完了したら、通常のリアクションはスキップ
                    
                    # ログリクエストを処理
                    import re
                    match = re.match(r'(\d{2}:\d{2})\s+(.+)', message.content)
                    if match:
                        requested_time = match.group(1)
                        log_message_content = match.group(2)
                        
                        # Botが代理でメッセージを投稿
                        post_message = f"{message.author.display_name} ({requested_time}): {log_message_content}"
                        target_channel = self.client.get_channel(self.target_channel_id)
                        if target_channel:
                            await target_channel.send(post_message)
                            await message.add_reaction('✅') # リクエスト元に確認リアクション
                            logger.info(f"ログリクエストを処理し、メッセージを投稿しました: {post_message}")
                        else:
                            logger.error(f"ログリクエストの投稿先チャンネル {self.target_channel_id} が見つかりません")
                            await message.add_reaction('❌')
                        return # ログリクエスト処理が完了したら、通常のリアクションはスキップ

                # 通常のリアクション（絵文字）
                await message.add_reaction('✅')

            except Exception as e:
                logger.error(f"監視処理でエラー: {e}")

        try:
            await self.client.start(self.bot_token)
        except Exception as e:
            logger.error(f"Discord 監視開始に失敗: {e}")

