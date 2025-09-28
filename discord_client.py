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
import asyncpg
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from config import Config

logger = logging.getLogger(__name__)

class DiscordMessageCollector:
    def __init__(self):
        self.bot_token = Config.DISCORD_BOT_TOKEN
        self.target_channel_id = Config.TARGET_CHANNEL_ID
        self.intents = discord.Intents.default()
        self.intents.message_content = True
        self.client = discord.Client(intents=self.intents)
        self.user_states = {} # ユーザーごとの会話状態を保持
        self.db_pool = None
        
        # キーワードとリアクションのマッピングを解析
        self.keyword_reactions = {}
        if Config.KEYWORD_REACTIONS:
            try:
                self.keyword_reactions = {
                    item.split(':')[0].strip(): item.split(':')[1].strip()
                    for item in Config.KEYWORD_REACTIONS.split(',')
                }
                logger.info(f"キーワードリアクションを読み込みました: {self.keyword_reactions}")
            except IndexError:
                logger.error("KEYWORD_REACTIONSのフォーマットが不正です。'key:value,key2:value2' の形式で設定してください。")
        
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
                
                # メッセージ内容を短縮し、キーワードを削除
                content = message['content']
                content = re.sub(r'(なう|わず|うぃる)', '', content).strip()
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
        """Botを終了し、DB接続を閉じる"""
        if self.db_pool:
            await self.db_pool.close()
            logger.info("データベース接続プールを閉じました。")
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
        """常時監視モードを開始。メッセージに応答"""

        @self.client.event
        async def on_ready():
            logger.info(f'{self.client.user} として監視を開始')
            await self.init_db() # DB初期化

        @self.client.event
        async def on_message(message: discord.Message):
            if message.author.id == self.client.user.id or not message.guild:
                return

            # キーワードリアクション処理
            for keyword, reaction in self.keyword_reactions.items():
                if keyword in message.content:
                    try:
                        await message.add_reaction(reaction)
                    except discord.HTTPException as e:
                        logger.warning(f"リアクションの追加に失敗しました: {reaction} ({e})")

            # 指定チャンネル以外は無視する設定（必要に応じてコメントアウト解除）
            # if message.channel.id != self.target_channel_id:
            #     return

            # Botへのメンションをチェック
            if self.client.user in message.mentions:
                mentioned_users = [user for user in message.mentions if user != self.client.user]
                if mentioned_users:
                    target_user = mentioned_users[0] # 最初のメンションユーザーを対象とする
                    logger.info(f"ユーザー {target_user.display_name} のサマリーリクエストを受信")

                    user_messages = await self.collect_messages_from_user_for_day(
                        user_id=target_user.id,
                        channel_id=message.channel.id
                    )

                    if user_messages:
                        summary_embed = self._format_summary_embed(user_messages)
                        await message.channel.send(f"{target_user.display_name}さんの本日のまとめです:", embed=summary_embed)
                    else:
                        await message.channel.send(f"{target_user.display_name}さんの本日のメッセージは見つかりませんでした。")
                    return # サマリー処理後は他のコマンドを評価しない

            user_id = message.author.id
            content = message.content.strip()

            # ユーザーの状態をチェック
            if user_id in self.user_states:
                state = self.user_states[user_id]
                state_type = state.get("type")

                if state_type == "add_storage":
                    await self.handle_add_storage_name(message, state)
                elif state_type == "add_item_storage":
                    await self.handle_add_item_storage_name(message, state)
                return

            # --- 会話形式のコマンド処理 ---
            if re.fullmatch(r"新しい収納を追加したい", content):
                self.user_states[user_id] = {"type": "add_storage"}
                await message.channel.send("いいよ！収納の名前は？")

            elif match := re.fullmatch(r"(.+)を登録したい", content):
                item_name = match.group(1)
                self.user_states[user_id] = {"type": "add_item_storage", "item_name": item_name}
                await message.channel.send("どの収納に入れる？")

            elif match := re.fullmatch(r"(.+)どこ？", content):
                item_name = match.group(1)
                await self.handle_find_item(message, item_name)

            elif match := re.fullmatch(r"(.+)の中身は？", content):
                storage_name = match.group(1)
                await self.handle_list_items_in_storage(message, storage_name)


        try:
            await self.client.start(self.bot_token)
        except Exception as e:
            logger.error(f"Discord 監視開始に失敗: {e}")
        finally:
            await self.close()


    async def init_db(self):
        """データベースを初期化し、テーブルを作成する"""
        self.db_pool = await asyncpg.create_pool(Config.DATABASE_URL)
        async with self.db_pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS guilds (
                    id BIGINT PRIMARY KEY,
                    name TEXT NOT NULL
                );
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS storages (
                    id SERIAL PRIMARY KEY,
                    guild_id BIGINT REFERENCES guilds(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    UNIQUE(guild_id, name)
                );
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS items (
                    id SERIAL PRIMARY KEY,
                    storage_id INT REFERENCES storages(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(storage_id, name)
                );
            ''')
        logger.info("データベースのテーブルを初期化しました。")


    async def handle_add_storage_name(self, message: discord.Message, state: dict):
        """収納名の入力を処理"""
        user_id = message.author.id
        storage_name = message.content.strip()
        guild_id = message.guild.id
        guild_name = message.guild.name

        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute("INSERT INTO guilds (id, name) VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET name = $2", guild_id, guild_name)
                await conn.execute("INSERT INTO storages (guild_id, name) VALUES ($1, $2)", guild_id, storage_name)
            await message.channel.send(f"『{storage_name}』を登録したよ！")
        except asyncpg.UniqueViolationError:
            await message.channel.send(f"『{storage_name}』はもうあるみたい。")
        except Exception as e:
            logger.error(f"収納の追加に失敗: {e}")
            await message.channel.send("ごめん、登録に失敗しちゃった。")
        finally:
            if user_id in self.user_states:
                del self.user_states[user_id]

    async def handle_add_item_storage_name(self, message: discord.Message, state: dict):
        """アイテムを入れる収納名の入力を処理"""
        user_id = message.author.id
        storage_name = message.content.strip()
        item_name = state["item_name"]
        guild_id = message.guild.id

        try:
            async with self.db_pool.acquire() as conn:
                storage_record = await conn.fetchrow("SELECT id FROM storages WHERE guild_id = $1 AND name = $2", guild_id, storage_name)
                if not storage_record:
                    await message.channel.send(f"『{storage_name}』っていう収納はないみたい。")
                    # 状態を維持して、再度入力を促すことも可能
                    return

                storage_id = storage_record['id']
                await conn.execute(
                    "INSERT INTO items (storage_id, name) VALUES ($1, $2) ON CONFLICT (storage_id, name) DO UPDATE SET updated_at = CURRENT_TIMESTAMP",
                    storage_id, item_name
                )
            await message.channel.send(f"『{item_name}』を『{storage_name}』に登録したよ！")
        except Exception as e:
            logger.error(f"アイテムの登録に失敗: {e}")
            await message.channel.send("ごめん、登録に失敗しちゃった。")
        finally:
            if user_id in self.user_states:
                del self.user_states[user_id]

    async def handle_find_item(self, message: discord.Message, item_name: str):
        """アイテムの場所を検索して返信"""
        guild_id = message.guild.id
        try:
            async with self.db_pool.acquire() as conn:
                result = await conn.fetchrow("""
                    SELECT s.name FROM items i
                    JOIN storages s ON i.storage_id = s.id
                    WHERE i.name = $1 AND s.guild_id = $2
                """, item_name, guild_id)

            if result:
                storage_name = result['name']
                await message.channel.send(f"『{item_name}』は『{storage_name}』にあるよ！")
            else:
                await message.channel.send(f"『{item_name}』は見つからないみたい。")
        except Exception as e:
            logger.error(f"アイテムの検索に失敗: {e}")
            await message.channel.send("ごめん、検索中にエラーが起きちゃった。")

    async def handle_list_items_in_storage(self, message: discord.Message, storage_name: str):
        """収納の中身を一覧表示"""
        guild_id = message.guild.id
        try:
            async with self.db_pool.acquire() as conn:
                results = await conn.fetch("""
                    SELECT i.name FROM items i
                    JOIN storages s ON i.storage_id = s.id
                    WHERE s.name = $1 AND s.guild_id = $2
                    ORDER BY i.name
                """, storage_name, guild_id)

            if results:
                item_names = [f"『{r['name']}』" for r in results]
                await message.channel.send("、".join(item_names) + "が入ってるよ！")
            else:
                await message.channel.send(f"『{storage_name}』には何もないみたい。")
        except Exception as e:
            logger.error(f"収納アイテムのリスト取得に失敗: {e}")
            await message.channel.send("ごめん、中身を確認中にエラーが起きちゃった。")


