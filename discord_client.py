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
        """すべてのチャンネルからキーワードを含むメッセージを収集"""
        all_messages = []
        
        try:
            if guild_id:
                guild = self.client.get_guild(guild_id)
                if not guild:
                    logger.error(f"ギルド {guild_id} が見つかりません")
                    return all_messages
                channels = guild.text_channels
            else:
                # すべてのギルドのテキストチャンネルを取得
                channels = []
                for guild in self.client.guilds:
                    channels.extend(guild.text_channels)
            
            for channel in channels:
                logger.info(f"チャンネル '{channel.name}' からメッセージを収集中...")
                messages = await self.collect_messages_from_channel(channel.id, days_back)
                all_messages.extend(messages)
                
                logger.info(f"チャンネル '{channel.name}' から {len(messages)} 件のメッセージを収集")
        
        except Exception as e:
            logger.error(f"メッセージ収集中にエラーが発生: {e}")
            
        return all_messages

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

                # リアクション（絵文字）
                await message.add_reaction('✅')

            except Exception as e:
                logger.error(f"監視処理でエラー: {e}")

        try:
            await self.client.start(self.bot_token)
        except Exception as e:
            logger.error(f"Discord 監視開始に失敗: {e}")

