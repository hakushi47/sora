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
from datetime import datetime, timedelta, timezone, time
from typing import List, Dict, Any, Optional
from config import Config

from discord.ext import commands, tasks
from discord import app_commands

logger = logging.getLogger(__name__)

class SoraBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.bot_token = Config.DISCORD_BOT_TOKEN
        self.target_channel_ids = Config.TARGET_CHANNEL_IDS
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

    async def on_ready(self):
        try:
            logger.info(f'{self.user} として監視を開始')
            await self.init_db() # DB初期化
            logger.info("データベースの初期化が完了しました。")

            await self.add_cog(FinanceCog(self))
            logger.info("FinanceCogをロードしました。")

            await self.tree.sync()
            logger.info("コマンドツリーを同期しました。Botの準備完了です！")

        except Exception as e:
            logger.error("on_readyで致命的なエラーが発生しました。", exc_info=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.id == self.user.id or not message.guild:
            return

        # --- Weekly Balance Check ---
        async with self.db_pool.acquire() as conn:
            check_state_record = await conn.fetchrow("SELECT state, last_input_balance FROM balance_check_state WHERE user_id = $1", message.author.id)

        if check_state_record and check_state_record['state']:
            state = check_state_record['state']
            content = message.content.strip()
            user_id = message.author.id

            if state == 'waiting_for_balance':
                if content.isdigit():
                    input_balance = int(content)
                    async with self.db_pool.acquire() as conn:
                        records = await conn.fetch("SELECT balance FROM user_balances WHERE user_id = $1 AND category IN ('ぬし財布', 'ぽて財布', '探検隊予算')", user_id)
                        current_balance = sum(r['balance'] for r in records)
                        diff = input_balance - current_balance

                        if diff == 0:
                            await message.channel.send("✅ 残高は一致している！今週もご苦労だったな。")
                            await conn.execute("UPDATE balance_check_state SET state = NULL, last_input_balance = NULL, last_checked_at = CURRENT_TIMESTAMP WHERE user_id = $1", user_id)
                        else:
                            await conn.execute("UPDATE balance_check_state SET state = 'waiting_for_reconciliation', last_input_balance = $1 WHERE user_id = $2", input_balance, user_id)
                            await message.channel.send(f"⚠️ **{abs(diff)}** 円の差異がある。記入漏れがないか確認し、以下のいずれかのコマンドを実行せよ：`!更新` または `!再入力 [修正後の残高]`")
                else:
                    await message.channel.send("おい隊員！有効な残高を半角数字で入力せよ！")
                return

            elif state == 'waiting_for_reconciliation':
                if content == '!更新':
                    last_input_balance = check_state_record['last_input_balance']
                    async with self.db_pool.acquire() as conn:
                        async with conn.transaction():
                            records = await conn.fetch("SELECT balance FROM user_balances WHERE user_id = $1 AND category IN ('ぬし財布', 'ぽて財布', '探検隊予算')", user_id)
                            current_balance = sum(r['balance'] for r in records)
                            diff = last_input_balance - current_balance

                            await conn.execute("INSERT INTO user_balances (user_id, category, balance) VALUES ($1, '探検隊予算', $2) ON CONFLICT (user_id, category) DO UPDATE SET balance = user_balances.balance + $2;", user_id, diff)
                            await conn.execute("INSERT INTO transactions (user_id, transaction_type, category, amount) VALUES ($1, 'adjustment', '残高調整', $2);", user_id, diff)
                            await conn.execute("UPDATE balance_check_state SET state = NULL, last_input_balance = NULL, last_checked_at = CURRENT_TIMESTAMP WHERE user_id = $1", user_id)
                    await message.channel.send(f"残高を **{last_input_balance}** 円に更新した。これが次回の基準となる。")

                elif content.startswith('!再入力 '):
                    new_balance_str = content.split(' ', 1)[1]
                    if new_balance_str.isdigit():
                        message.content = new_balance_str
                        async with self.db_pool.acquire() as conn:
                            await conn.execute("UPDATE balance_check_state SET state = 'waiting_for_balance' WHERE user_id = $1", user_id)
                        await self.on_message(message)
                    else:
                        await message.channel.send("おい隊員！再入力する残高は半角数字で頼む！")
                else:
                    await message.channel.send("`!更新` または `!再入力 [数字]` の形式でコマンドを実行してくれ。")
                return

        if message.channel.id not in self.target_channel_ids:
            return

        await self._log_message_to_db(message)

        for keyword, reaction in self.keyword_reactions.items():
            if keyword in message.content:
                try:
                    await message.add_reaction(reaction)
                except discord.HTTPException as e:
                    logger.warning(f"リアクションの追加に失敗しました: {reaction} ({e})")

        if self.user in message.mentions:
            mentioned_users = [user for user in message.mentions if user != self.user]
            if mentioned_users:
                target_user = mentioned_users[0]
                logger.info(f"ユーザー {target_user.display_name} のサマリーリクエストを受信")
                user_messages = await self.collect_messages_from_user_for_day(user_id=target_user.id, channel_id=message.channel.id)
                if user_messages:
                    summary_embed = self._format_summary_embed(user_messages)
                    await message.channel.send(f"{target_user.display_name}さんの本日のまとめです:", embed=summary_embed)
                else:
                    await message.channel.send(f"{target_user.display_name}さんの本日のメッセージは見つかりませんでした。")
                return

        user_id = message.author.id
        content = message.content.strip()

        if user_id in self.user_states:
            state = self.user_states[user_id]
            state_type = state.get("type")
            if state_type == "add_storage":
                await self.handle_add_storage_name(message, state)
            elif state_type == "add_item_storage":
                await self.handle_add_item_storage_name(message, state)
            return

        if (match := re.fullmatch(r"(\d{1,2}):(\d{2})\s+(.+)わず", content)):
            await self.handle_activity(message, match, 'done')
            return
        elif (match := re.fullmatch(r"(.+)なう", content)):
            await self.handle_activity(message, match, 'doing')
            return
        elif (match := re.fullmatch(r"(\d{1,2}):(\d{2})\s+(.+)うぃる", content)):
            await self.handle_activity(message, match, 'todo')
            return

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

    def run_bot(self):
        self.run(self.bot_token)
        
    async def collect_messages_from_channel(self, channel_id: int, days_back: int = 1) -> List[Dict[str, Any]]:
        """指定されたチャンネルからメッセージを収集"""
        collected_messages = []
        try:
            channel = self.get_channel(channel_id)
            if not channel:
                logger.error(f"チャンネル {channel_id} が見つかりません")
                return collected_messages
            after_date = datetime.now() - timedelta(days=days_back)
            async for message in channel.history(after=after_date, limit=1000):
                collected_messages.append({
                    'channel_id': channel_id, 'channel_name': channel.name, 'user_id': message.author.id,
                    'username': message.author.display_name or message.author.name, 'content': message.content,
                    'timestamp': message.created_at.timestamp(), 'message_id': message.id, 'jump_url': message.jump_url,
                    'guild_id': message.guild.id if message.guild else None, 'guild_name': message.guild.name if message.guild else None
                })
        except Exception as e:
            logger.error(f"チャンネル {channel_id} からのメッセージ取得に失敗: {e}")
        return collected_messages
    
    async def collect_all_messages(self, guild_id: int = None, days_back: int = 1) -> List[Dict[str, Any]]:
        """指定チャンネルからメッセージを収集"""
        all_messages = []
        try:
            logger.info(f"チャンネル '{self.target_channel_id}' からメッセージを収集中...")
            messages = await self.collect_messages_from_channel(self.target_channel_id, days_back)
            all_messages.extend(messages)
            logger.info(f"チャンネル '{self.target_channel_id}' から {len(messages)} 件のメッセージを収集")
        except Exception as e:
            logger.error(f"メッセージ収集中にエラーが発生: {e}")
        return all_messages

    async def collect_messages_from_user_for_day(self, user_id: int, channel_id: int) -> List[Dict[str, Any]]:
        """指定されたユーザーのその日のメッセージをDBから収集"""
        collected_messages = []
        try:
            jst = timezone(timedelta(hours=9))
            today_jst = datetime.now(jst).date()
            start_of_day_jst = datetime.combine(today_jst, time.min, tzinfo=jst)
            async with self.db_pool.acquire() as conn:
                records = await conn.fetch("""
                    SELECT content, created_at, id FROM messages
                    WHERE user_id = $1 AND channel_id = $2 AND created_at >= $3 ORDER BY created_at ASC
                """, user_id, channel_id, start_of_day_jst)
            channel = self.get_channel(channel_id)
            guild = channel.guild if channel else None
            author = await self.fetch_user(user_id) if self.get_user(user_id) is None else self.get_user(user_id)
            bot_mention_pattern = f"<@{self.user.id}>"
            for record in records:
                if bot_mention_pattern in record['content']: continue
                created_at_aware = record['created_at'].astimezone(jst)
                if created_at_aware.date() != today_jst: continue
                collected_messages.append({
                    'channel_id': channel_id, 'channel_name': channel.name if channel else 'Unknown Channel',
                    'user_id': user_id, 'username': author.display_name if author else 'Unknown User',
                    'content': record['content'], 'datetime_obj': created_at_aware, 'message_id': record['id'],
                    'jump_url': f"https://discord.com/channels/{guild.id if guild else '@me'}/{channel_id}/{record['id']}",
                    'guild_id': guild.id if guild else None, 'guild_name': guild.name if guild else None
                })
        except Exception as e:
            logger.error(f"ユーザー {user_id} のメッセージ収集に失敗(DB): {e}", exc_info=True)
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
            return discord.Embed(title="📝 収集サマリー", description="メッセージは見つかりませんでした。", color=0x00ff00, timestamp=datetime.now())
        channel_groups = {}
        for message in messages:
            channel_name = message['channel_name']
            if channel_name not in channel_groups: channel_groups[channel_name] = []
            channel_groups[channel_name].append(message)
        embed = discord.Embed(title="📝 収集サマリー", description=f"**収集件数**: {len(messages)}件", color=0x00ff00, timestamp=datetime.now())
        for channel_name, channel_messages in channel_groups.items():
            field_value = ""
            for message in channel_messages[:10]:
                time_str = message['datetime_obj'].strftime('%H:%M')
                content = re.sub(r'(なう|わず|うぃる)', '', message['content']).strip()
                if len(content) > 100: content = content[:100] + "..."
                field_value += f"**{time_str}** {message['username']}: {content}\n"
            if len(channel_messages) > 10: field_value += f"...他{len(channel_messages) - 10}件"
            if field_value:
                embed.add_field(name=f"#{channel_name} ({len(channel_messages)}件)", value=field_value, inline=False)
        return embed
    
    async def start_bot(self):
        """Discord Botを開始（常駐）"""
        @self.event
        async def on_ready():
            logger.info(f'{self.user} としてログインしました')
            logger.info(f'接続中のギルド数: {len(self.guilds)}')
        try:
            await self.start(self.bot_token)
        except Exception as e:
            logger.error(f"Discord Botの開始に失敗: {e}")
    
    async def close(self):
        """Botを終了し、DB接続を閉じる"""
        if self.db_pool:
            await self.db_pool.close()
            logger.info("データベース接続プールを閉じました。")
        await super().close()

    async def run_once_collect_and_post(self, days_back: int = 1) -> bool:
        """ログイン→収集→投稿→終了までを一度で実行"""
        done_flag = {'ran': False, 'success': True}
        @self.event
        async def on_ready():
            logger.info(f'{self.user} としてログインしました')
            try:
                messages = await self.collect_all_messages(guild_id=Config.GUILD_ID, days_back=days_back)
                if not messages: logger.info("収集されたメッセージがありません")
                else:
                    logger.info(f"{len(messages)}件のメッセージを収集しました")
                    await self.post_summary(messages)
                done_flag['ran'] = True
            except Exception as e:
                done_flag['success'] = False
                logger.error(f"収集/投稿処理でエラー: {e}")
            finally: await self.close()
        try:
            await self.start(self.bot_token)
        except Exception as e:
            logger.error(f"Discord Botの開始に失敗: {e}")
            return False
        return done_flag['success'] and done_flag['ran']

    async def start_monitor(self):
        """常時監視モードを開始。メッセージに応答"""
        @self.event
        async def on_ready():
            logger.info(f'{self.user} として監視を開始')
            await self.init_db()
        # on_message is now handled by the main class listener
        try:
            await self.start(self.bot_token)
        except Exception as e:
            logger.error(f"Discord 監視開始に失敗: {e}")
        finally: await self.close()

    async def init_db(self):
        """データベースを初期化し、テーブルを作成する"""
        self.db_pool = await asyncpg.create_pool(Config.DATABASE_URL)
        async with self.db_pool.acquire() as conn:
            await conn.execute('''CREATE TABLE IF NOT EXISTS guilds (id BIGINT PRIMARY KEY, name TEXT NOT NULL);''')
            await conn.execute('''CREATE TABLE IF NOT EXISTS storages (id SERIAL PRIMARY KEY, guild_id BIGINT REFERENCES guilds(id) ON DELETE CASCADE, name TEXT NOT NULL, UNIQUE(guild_id, name));''')
            await conn.execute('''CREATE TABLE IF NOT EXISTS items (id SERIAL PRIMARY KEY, storage_id INT REFERENCES storages(id) ON DELETE CASCADE, name TEXT NOT NULL, updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(storage_id, name));''')
        logger.info("データベースのテーブルを初期化しました。")

        async with self.db_pool.acquire() as conn:
            await conn.execute('''CREATE TABLE IF NOT EXISTS messages (id BIGINT PRIMARY KEY, guild_id BIGINT, channel_id BIGINT, user_id BIGINT, content TEXT, created_at TIMESTAMP WITH TIME ZONE);''')
        logger.info("messagesテーブルを初期化しました。")

        async with self.db_pool.acquire() as conn:
            await conn.execute('''CREATE TABLE IF NOT EXISTS user_balances (user_id BIGINT NOT NULL, category TEXT NOT NULL, balance BIGINT NOT NULL, PRIMARY KEY (user_id, category));''')
            await conn.execute('''CREATE TABLE IF NOT EXISTS transactions (id SERIAL PRIMARY KEY, user_id BIGINT NOT NULL, transaction_type TEXT NOT NULL, category TEXT, amount BIGINT NOT NULL, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);''')
            await conn.execute('''CREATE TABLE IF NOT EXISTS balance_check_state (user_id BIGINT PRIMARY KEY, state TEXT, last_input_balance BIGINT, last_checked_at TIMESTAMP WITH TIME ZONE);''')
        logger.info("家計簿機能のテーブルを初期化しました。")

        async with self.db_pool.acquire() as conn:
            await conn.execute('DROP TABLE IF EXISTS past_activities;')
            await conn.execute('''CREATE TABLE IF NOT EXISTS activities (id SERIAL PRIMARY KEY, user_id BIGINT NOT NULL, channel_id BIGINT NOT NULL, guild_id BIGINT NOT NULL, content TEXT NOT NULL, activity_time TIMESTAMP WITH TIME ZONE NOT NULL, status TEXT NOT NULL, original_message_id BIGINT);''')
        logger.info("活動記録テーブル(activities)を初期化しました。")

    async def _log_message_to_db(self, message: discord.Message):
        """メッセージをデータベースに記録する"""
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO messages (id, guild_id, channel_id, user_id, content, created_at) VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (id) DO NOTHING",
                    message.id, message.guild.id, message.channel.id, message.author.id, message.content, message.created_at,
                )
        except Exception as e:
            logger.error(f"メッセージのデータベースへの記録に失敗: {e}")

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
            if user_id in self.user_states: del self.user_states[user_id]

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
                    return
                storage_id = storage_record['id']
                await conn.execute("INSERT INTO items (storage_id, name) VALUES ($1, $2) ON CONFLICT (storage_id, name) DO UPDATE SET updated_at = CURRENT_TIMESTAMP", storage_id, item_name)
            await message.channel.send(f"『{item_name}』を『{storage_name}』に登録したよ！")
        except Exception as e:
            logger.error(f"アイテムの登録に失敗: {e}")
            await message.channel.send("ごめん、登録に失敗しちゃった。")
        finally:
            if user_id in self.user_states: del self.user_states[user_id]

    async def handle_find_item(self, message: discord.Message, item_name: str):
        """アイテムの場所を検索して返信"""
        guild_id = message.guild.id
        try:
            async with self.db_pool.acquire() as conn:
                result = await conn.fetchrow("SELECT s.name FROM items i JOIN storages s ON i.storage_id = s.id WHERE i.name = $1 AND s.guild_id = $2", item_name, guild_id)
            if result:
                await message.channel.send(f"『{item_name}』は『{result['name']}』にあるよ！")
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
                results = await conn.fetch("SELECT i.name FROM items i JOIN storages s ON i.storage_id = s.id WHERE s.name = $1 AND s.guild_id = $2 ORDER BY i.name", storage_name, guild_id)
            if results:
                item_names = [f"『{r['name']}』" for r in results]
                await message.channel.send("、".join(item_names) + "が入ってるよ！")
            else:
                await message.channel.send(f"『{storage_name}』には何もないみたい。")
        except Exception as e:
            logger.error(f"収納アイテムのリスト取得に失敗: {e}")
            await message.channel.send("ごめん、中身を確認中にエラーが起きちゃった。")

    async def handle_activity(self, message: discord.Message, match: re.Match, status: str):
        """活動記録を処理する (わず, なう, うぃる)"""
        try:
            activity_time, content = None, ""
            if status == 'done' or status == 'todo':
                hour, minute, content = int(match.group(1)), int(match.group(2)), match.group(3).strip()
                base_time = message.created_at
                activity_time = base_time.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if status == 'done' and activity_time > base_time: activity_time -= timedelta(days=1)
                elif status == 'todo' and activity_time < base_time: activity_time += timedelta(days=1)
            elif status == 'doing':
                content, activity_time = match.group(1).strip(), message.created_at
            if activity_time is None:
                await message.add_reaction("🤔")
                return
            async with self.db_pool.acquire() as conn:
                await conn.execute("INSERT INTO activities (user_id, channel_id, guild_id, content, activity_time, status, original_message_id) VALUES ($1, $2, $3, $4, $5, $6, $7)",
                                   message.author.id, message.channel.id, message.guild.id, content, activity_time, status, message.id)
            await message.add_reaction("✅")
        except ValueError: await message.add_reaction("🤔")
        except Exception as e:
            logger.error(f"活動の記録に失敗: {e}")
            await message.add_reaction("❌")

import random

def get_captain_quote(category: str) -> str:
    quotes = {
        "salary": ["今日の給料だな！よくやった！これで次の冒険の準備ができるぞ！", "報酬だ！隊員の働きに感謝する！", "よし、今日の稼ぎだな！財源は探検隊の命綱だ！", "新たな資金源の確保、ご苦労！これでまた一歩前進だな！", "よろしい！隊の財政が潤ったな。次の任務に備えよ！", "うむ、見事な稼ぎだ！隊の活動は盤石だな！"],
        "spend": ["冒険のための投資だな！無駄遣いではない、戦略的支出だ！", "よし、必要な出費だな！次の補給も計画的にな！", "備品は大事に扱えよ！それが一流の冒険者というものだ！", "戦略的投資、承認する！未来への布石となるだろう！", "必要な物資の確保は重要だ。抜かりないな、隊員！", "出費は最小限に、効果は最大限に。基本を忘れるな！"],
        "balance": ["現在の財産だな。常に状況を把握しておくことは隊長の務めだ！", "これが我々の現在の戦力だ！無駄遣いは許さん！", "よし、財産の確認だな。次の探検計画を練るぞ！", "財政状況の報告、感謝する。常に数字は正確にな！", "よし、現状を把握した。これをもとに次なる一手を打つ！", "ふむ、これが我々の現在地か。心してかかれ！"],
        "report": ["今週の活動報告だな。よくやった！", "月間報告ご苦労！隊の活動は順調そのものだ！", "報告感謝する。次の冒険への良い指針となるだろう！", "活動報告、拝見した。隊員たちの働き、見事の一言だ！", "素晴らしい報告だ！この調子で任務を遂行せよ！", "ご苦労だったな。このデータが我々の道を照らすだろう！"]
    }
    return random.choice(quotes.get(category, ["よくやったな！その調子だ！"]))

class FinanceCog(commands.Cog):
    def __init__(self, bot: SoraBot):
        self.bot = bot

    @tasks.loop(time=time(20, 0, tzinfo=timezone(timedelta(hours=9))))
    async def weekly_balance_check(self):
        today = datetime.now(self.jst)
        if today.weekday() != 4: # 4:金曜日
            return
        
        channel_id = self.bot.target_channel_ids[0]
        channel = self.bot.get_channel(channel_id)
        if not channel:
            logger.error(f"残高チェック用のチャンネル {channel_id} が見つからん！")
            return

        start_of_week = (today - timedelta(days=today.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)

        async with self.bot.db_pool.acquire() as conn:
            user_records = await conn.fetch("SELECT DISTINCT user_id FROM user_balances")
            prompt_sent = False
            for record in user_records:
                user_id = record['user_id']
                check_state = await conn.fetchrow("SELECT last_checked_at FROM balance_check_state WHERE user_id = $1", user_id)
                
                if check_state and check_state['last_checked_at'] and check_state['last_checked_at'] >= start_of_week:
                    logger.info(f"ユーザー {user_id} は今週既にチェック済みのためスキップする。")
                    continue

                if not prompt_sent:
                    await channel.send("🚨 毎週の残高チェックの時間だ！財布（ぬし財布、ぽて財布、探検隊予算）の現在の合計残高を半角数字で入力せよ！")
                    prompt_sent = True

                await conn.execute('''
                    INSERT INTO balance_check_state (user_id, state) VALUES ($1, 'waiting_for_balance')
                    ON CONFLICT (user_id) DO UPDATE SET state = 'waiting_for_balance', last_input_balance = NULL;
                ''', user_id)
        if prompt_sent:
            logger.info("残高チェックが必要な隊員への通知を完了した。")

    @app_commands.command(name="check_balance_manual", description="週次の残高チェックを手動で開始するぞ！")
    async def check_balance_manual(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        async with self.bot.db_pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO balance_check_state (user_id, state) VALUES ($1, 'waiting_for_balance')
                ON CONFLICT (user_id) DO UPDATE SET state = 'waiting_for_balance', last_input_balance = NULL;
            ''', user_id)
        await interaction.response.send_message("🚨 残高チェックを開始する！財布（ぬし財布、ぽて財布、探検隊予算）の現在の合計残高を半角数字で入力せよ！", ephemeral=True)

    @app_commands.command(name="balance", description="現在の全財産を確認するぞ！")
    async def balance(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        async with self.bot.db_pool.acquire() as conn:
            records = await conn.fetch("SELECT category, balance FROM user_balances WHERE user_id = $1", user_id)
        
        balance_data = {record['category']: record['balance'] for record in records}
        pote_wallet = balance_data.get("ぽて財布", 0)
        nushi_wallet = balance_data.get("ぬし財布", 0)
        savings = balance_data.get("貯金", 0)
        expedition_budget = balance_data.get("探検隊予算", 0)
        living_expenses = pote_wallet + nushi_wallet

        message = (
            f"👩 ぽて財布: {pote_wallet}円\n"
            f"👨 ぬし財布: {nushi_wallet}円\n"
            f"🏠 生活費残り: {living_expenses}円\n"
            f"🐷 貯金: {savings}円\n"
            f"🛡 探検隊予算: {expedition_budget}円\n"
            f"→ {get_captain_quote('balance')}"
        )
        await interaction.response.send_message(message)

    @app_commands.command(name="salary", description="給料を受け取り、自動で割り振るぞ！")
    @app_commands.describe(amount="受け取った給料の金額")
    async def salary(self, interaction: discord.Interaction, amount: int):
        if amount <= 0:
            await interaction.response.send_message("おい隊員！給料は正の整数で頼む！")
            return

        user_id = interaction.user.id
        nushi_wallet_amount = amount // 2
        savings_amount = (amount * 3) // 10
        expedition_budget_amount = (amount * 2) // 10
        pote_wallet_amount = 0
        
        remainder = amount - nushi_wallet_amount - savings_amount - expedition_budget_amount
        savings_amount += remainder

        async with self.bot.db_pool.acquire() as conn:
            async with conn.transaction():
                for category, cat_amount in [("ぬし財布", nushi_wallet_amount), ("貯金", savings_amount), ("探検隊予算", expedition_budget_amount)]:
                    if cat_amount > 0:
                        await conn.execute("INSERT INTO user_balances (user_id, category, balance) VALUES ($1, $2, $3) ON CONFLICT (user_id, category) DO UPDATE SET balance = user_balances.balance + $3;", user_id, category, cat_amount)
                await conn.execute("INSERT INTO transactions (user_id, transaction_type, category, amount) VALUES ($1, 'salary', '給料', $2);", user_id, amount)

        message = (
            f"💰 給料 {amount}円を受け取り、割り振ったぞ！\n"
            f"👩 ぽて財布: +{pote_wallet_amount}円\n"
            f"👨 ぬし財布: +{nushi_wallet_amount}円\n"
            f"🐷 貯金: +{savings_amount}円\n"
            f"🛡 探検隊予算: +{expedition_budget_amount}円\n"
            f"→ {get_captain_quote('salary')}"
        )
        await interaction.response.send_message(message)

    @app_commands.command(name="transfer", description="財布・貯金・予算の間で資金を移動するぞ！")
    @app_commands.describe(amount="移動する金額", from_wallet="移動元の財布/カテゴリ", to_wallet="移動先の財布/カテゴリ")
    @app_commands.choices(from_wallet=[app_commands.Choice(name="ぽて財布", value="ぽて財布"), app_commands.Choice(name="ぬし財布", value="ぬし財布"), app_commands.Choice(name="貯金", value="貯金"), app_commands.Choice(name="探検隊予算", value="探検隊予算")], 
                          to_wallet=[app_commands.Choice(name="ぽて財布", value="ぽて財布"), app_commands.Choice(name="ぬし財布", value="ぬし財布"), app_commands.Choice(name="貯金", value="貯金"), app_commands.Choice(name="探検隊予算", value="探検隊予算")])
    async def transfer(self, interaction: discord.Interaction, amount: int, from_wallet: app_commands.Choice[str], to_wallet: app_commands.Choice[str]):
        if amount <= 0:
            await interaction.response.send_message("おい隊員！移動する金額は正の整数で頼む！")
            return
        user_id, from_name, to_name = interaction.user.id, from_wallet.value, to_wallet.value
        if from_name == to_name:
            await interaction.response.send_message("移動元と移動先が同じだぞ！確認しろ！")
            return

        async with self.bot.db_pool.acquire() as conn:
            async with conn.transaction():
                current_balance_record = await conn.fetchrow("SELECT balance FROM user_balances WHERE user_id = $1 AND category = $2", user_id, from_name)
                current_balance = current_balance_record['balance'] if current_balance_record else 0
                if current_balance < amount:
                    await interaction.response.send_message(f"おい隊員！ {from_name}の残高が足りないぞ！ (現在: {current_balance}円)")
                    return
                await conn.execute("UPDATE user_balances SET balance = balance - $1 WHERE user_id = $2 AND category = $3", amount, user_id, from_name)
                await conn.execute("INSERT INTO user_balances (user_id, category, balance) VALUES ($1, $2, $3) ON CONFLICT (user_id, category) DO UPDATE SET balance = user_balances.balance + $3", user_id, to_name, amount)
                await conn.execute("INSERT INTO transactions (user_id, transaction_type, category, amount) VALUES ($1, 'transfer', $2, $3);", user_id, f"{from_name}→{to_name}", amount)
        
        await interaction.response.send_message(f"🔄 {from_name} から {to_name} へ {amount}円を移動したぞ！\n→ 資金の再配分、見事だ！")

    @app_commands.command(name="spend", description="支出を記録するぞ！")
    @app_commands.describe(amount="支出した金額", category="支出の内容", from_wallet="どの財布から支払ったか")
    @app_commands.choices(category=[app_commands.Choice(name="食費", value="食費"), app_commands.Choice(name="日用品費", value="日用品費"), app_commands.Choice(name="交通費", value="交通費"), app_commands.Choice(name="家賃", value="家賃"), app_commands.Choice(name="交際費", value="交際費"), app_commands.Choice(name="娯楽費", value="娯楽費"), app_commands.Choice(name="医療費", value="医療費"), app_commands.Choice(name="その他", value="その他")], 
                          from_wallet=[app_commands.Choice(name="ぽて財布", value="ぽて財布"), app_commands.Choice(name="ぬし財布", value="ぬし財布"), app_commands.Choice(name="探検隊予算", value="探検隊予算")])
    async def spend(self, interaction: discord.Interaction, amount: int, category: app_commands.Choice[str], from_wallet: app_commands.Choice[str]):
        if amount <= 0:
            await interaction.response.send_message("おい隊員！支出は正の整数で頼む！")
            return
        user_id, category_name, source_wallet_name = interaction.user.id, category.value, from_wallet.value
        async with self.bot.db_pool.acquire() as conn:
            async with conn.transaction():
                current_balance_record = await conn.fetchrow("SELECT balance FROM user_balances WHERE user_id = $1 AND category = $2", user_id, source_wallet_name)
                current_balance = current_balance_record['balance'] if current_balance_record else 0
                if current_balance < amount:
                    await interaction.response.send_message(f"おい隊員！ {source_wallet_name}の残高が足りないぞ！ (現在: {current_balance}円)")
                    return
                await conn.execute("UPDATE user_balances SET balance = balance - $1 WHERE user_id = $2 AND category = $3", amount, user_id, source_wallet_name)
                await conn.execute("INSERT INTO transactions (user_id, transaction_type, category, amount) VALUES ($1, 'spend', $2, $3);", user_id, category_name, amount)
        
        icon = {"ぽて財布": "👩", "ぬし財布": "👨", "探検隊予算": "🛡"}.get(source_wallet_name, "💰")
        await interaction.response.send_message(f"{icon} {source_wallet_name}から【{category_name}】として {amount}円を消費\n→ {get_captain_quote('spend')}")

    @app_commands.command(name="report", description="指定した期間の収支報告書を作成するぞ！")
    @app_commands.describe(period="報告の期間 (week/month)")
    @app_commands.choices(period=[app_commands.Choice(name="今週", value="week"), app_commands.Choice(name="今月", value="month")])
    async def report(self, interaction: discord.Interaction, period: app_commands.Choice[str]):
        user_id, period_name = interaction.user.id, period.value
        now = datetime.now()
        if period_name == 'week':
            start_date = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            title = "今週の探検隊活動まとめ"
        else:
            start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            title = "今月の探検隊活動まとめ"
        
        async with self.bot.db_pool.acquire() as conn:
            transactions = await conn.fetch("SELECT transaction_type, category, amount FROM transactions WHERE user_id = $1 AND created_at >= $2", user_id, start_date)
        
        total_salary = sum(t['amount'] for t in transactions if t['transaction_type'] == 'salary')
        spend_by_category = {cat: sum(t['amount'] for t in transactions if t['transaction_type'] == 'spend' and t['category'] == cat) for cat in ["食費", "日用品費", "交通費", "家賃", "交際費", "娯楽費", "医療費", "その他"]}
        total_spend = sum(spend_by_category.values())

        message = (
            f"📅 {title}\n"
            f"💰 総収入: {total_salary}円\n"
            f"💸 総支出: {total_spend}円\n"
            f"--- 支出内訳 ---\n"
            + "".join([f" - {cat}: {amount}円\n" for cat, amount in spend_by_category.items() if amount > 0]) +
            f"→ {get_captain_quote('report')}"
        )
        await interaction.response.send_message(message)

    @app_commands.command(name="scan_past_activities", description="過去のメッセージをスキャンして、記録漏れの「わず」活動を登録します。")
    @app_commands.describe(days_back="何日前まで遡ってスキャンしますか？（デフォルト: 7）")
    async def scan_past_activities(self, interaction: discord.Interaction, days_back: int = 7):
        await interaction.response.defer(thinking=True)
        target_channel, after_date, count = interaction.channel, datetime.now() - timedelta(days=days_back), 0
        try:
            async with self.bot.db_pool.acquire() as conn:
                recorded_ids_result = await conn.fetchval("SELECT array_agg(original_message_id) FROM activities WHERE original_message_id IS NOT NULL")
                recorded_ids = set(recorded_ids_result or [])
            
            async for message in target_channel.history(limit=None, after=after_date):
                if message.id in recorded_ids: continue
                content = message.content.strip()
                if (match := re.fullmatch(r"(\d{1,2}):(\d{2})\s+(.+)わず", content)):
                    await self.bot.handle_activity(message, match, 'done')
                    count += 1
                elif (match := re.fullmatch(r"(.+)なう", content)):
                    await self.bot.handle_activity(message, match, 'doing')
                    count += 1
                elif (match := re.fullmatch(r"(\d{1,2}):(\d{2})\s+(.+)うぃる", content)):
                    await self.bot.handle_activity(message, match, 'todo')
                    count += 1
            
            await interaction.followup.send(f"スキャン完了！ 新しく {count} 件の活動を記録した。")
        except Exception as e:
            logger.error(f"過去活動のスキャンに失敗: {e}")
            await interaction.followup.send("すまん、スキャン中にエラーが発生した。")
