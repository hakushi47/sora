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

WALLET_ORDER = ["ぬし財布", "ぽて財布", "探検隊予算", "貯金"]

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

    async def setup_hook(self):
        # Cogのロード
        await self.add_cog(FinanceCog(self))
        logger.info("FinanceCogをロードしました。")

        # コマンドの同期
        if Config.GUILD_ID:
            guild = discord.Object(id=Config.GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info(f"コマンドをギルド {Config.GUILD_ID} に同期しました。")
        else:
            await self.tree.sync()
            logger.info("グローバルコマンドを同期しました。")

    async def on_ready(self):
        try:
            logger.info(">>>>>> 新バージョンのコードが正常に起動しました！<<<<<<")
            logger.info(f'{self.user} として監視を開始')
            await self.init_db() # DB初期化
            logger.info("データベースの初期化が完了しました。")
            logger.info("Botの準備完了です！")

        except Exception as e:
            logger.error("on_readyで致命的なエラーが発生しました。", exc_info=True)

    async def on_message(self, message: discord.Message):
        if message.author.id == self.user.id or not message.guild:
            return

        user_id = message.author.id
        content = message.content.strip()

        if content.startswith("spend_webhook:"):
            await self.handle_spend_webhook(message)
            return

        # --- Step-by-step Weekly Balance Check ---
        async with self.db_pool.acquire() as conn:
            check_state_record = await conn.fetchrow("SELECT * FROM balance_check_state WHERE user_id = $1", user_id)

        if check_state_record and check_state_record['state'] and check_state_record['state'].startswith('waiting_for_balance_'):
            wallet_name = check_state_record['state'].replace('waiting_for_balance_', '')
            current_wallet_index = WALLET_ORDER.index(wallet_name)

            if not content.isdigit():
                await message.channel.send(f"おい隊員！有効な残高を半角数字で入力せよ！")
                return

            input_balance = int(content)
            column_map = {"ぬし財布": "input_nushi", "ぽて財布": "input_pote", "探検隊予算": "input_budget", "貯金": "input_savings"}
            db_column_name = column_map[wallet_name]

            async with self.db_pool.acquire() as conn:
                await conn.execute(f"UPDATE balance_check_state SET {db_column_name} = $1 WHERE user_id = $2", input_balance, user_id)

                if current_wallet_index < len(WALLET_ORDER) - 1:
                    next_wallet_name = WALLET_ORDER[current_wallet_index + 1]
                    next_state = f"waiting_for_balance_{next_wallet_name}"
                    await conn.execute("UPDATE balance_check_state SET state = $1 WHERE user_id = $2", next_state, user_id)
                    await message.channel.send(f"了解した。次に【{next_wallet_name}】の残高を入力せよ！")
                else:
                    # Final step, calculate differences
                    await conn.execute("UPDATE balance_check_state SET state = 'waiting_for_reconciliation' WHERE user_id = $1", user_id)
                    final_inputs = await conn.fetchrow("SELECT * FROM balance_check_state WHERE user_id = $1", user_id)
                    db_balances_records = await conn.fetch("SELECT category, balance FROM user_balances WHERE user_id = $1", user_id)
                    db_balances = {r['category']: r['balance'] for r in db_balances_records}

                    input_balances = {
                        "ぬし財布": final_inputs['input_nushi'] or 0,
                        "ぽて財布": final_inputs['input_pote'] or 0,
                        "探検隊予算": final_inputs['input_budget'] or 0,
                        "貯金": final_inputs['input_savings'] or 0,
                    }

                    diff_messages = []
                    total_diff = 0
                    for wallet in WALLET_ORDER:
                        db_val = db_balances.get(wallet, 0)
                        input_val = input_balances.get(wallet, 0)
                        diff = input_val - db_val
                        total_diff += diff
                        if diff != 0:
                            diff_messages.append(f"【{wallet}】: {diff:+}円")

                    if total_diff == 0:
                        await message.channel.send("✅ 全ての残高が一致した！完璧だ！今週のチェックを完了とする。")
                        await conn.execute("UPDATE balance_check_state SET state = NULL, last_checked_at = CURRENT_TIMESTAMP WHERE user_id = $1", user_id)
                    else:
                        response = f"⚠️ 合計で **{total_diff:+}円** の差異があるぞ。\n**内訳:**\n" + "\n".join(diff_messages)
                        response += "\n\n問題なければ `!更新` を、最初からやり直す場合は `!再入力` を実行せよ。"
                        await message.channel.send(response)
            return

        elif check_state_record and check_state_record['state'] == 'waiting_for_reconciliation':
            if content == '!更新':
                async with self.db_pool.acquire() as conn:
                    inputs = await conn.fetchrow("SELECT * FROM balance_check_state WHERE user_id = $1", user_id)
                    input_balances = {
                        "ぬし財布": inputs['input_nushi'],
                        "ぽて財布": inputs['input_pote'],
                        "探検隊予算": inputs['input_budget'],
                        "貯金": inputs['input_savings'],
                    }
                    async with conn.transaction():
                        for wallet, new_balance in input_balances.items():
                            if new_balance is not None:
                                await conn.execute("INSERT INTO user_balances (user_id, category, balance) VALUES ($1, $2, $3) ON CONFLICT (user_id, category) DO UPDATE SET balance = $3", user_id, wallet, new_balance)
                        await conn.execute("UPDATE balance_check_state SET state = NULL, last_checked_at = CURRENT_TIMESTAMP WHERE user_id = $1", user_id)
                await message.channel.send("✅ 全ての財布の残高を更新した。これで記録は現実と一致したはずだ。")
            elif content == '!再入力':
                async with self.db_pool.acquire() as conn:
                    await conn.execute("UPDATE balance_check_state SET state = 'waiting_for_balance_ぬし財布' WHERE user_id = $1", user_id)
                await message.channel.send("了解した。最初からやり直す。まず【ぬし財布】の残高を入力せよ！")
            else:
                await message.channel.send("`!更新` または `!再入力` の形式でコマンドを実行してくれ。")
            return

        # --- End of Balance Check ---

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

        await self.process_commands(message)

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
            await conn.execute('''CREATE TABLE IF NOT EXISTS messages (id BIGINT PRIMARY KEY, guild_id BIGINT, channel_id BIGINT, user_id BIGINT, content TEXT, created_at TIMESTAMP WITH TIME ZONE);''')
            await conn.execute('''CREATE TABLE IF NOT EXISTS user_balances (user_id BIGINT NOT NULL, category TEXT NOT NULL, balance BIGINT NOT NULL, PRIMARY KEY (user_id, category));''')
            await conn.execute('''CREATE TABLE IF NOT EXISTS transactions (id SERIAL PRIMARY KEY, user_id BIGINT NOT NULL, transaction_type TEXT NOT NULL, category TEXT, amount BIGINT NOT NULL, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);''')

            # transactionsテーブルにカラムが存在するか確認し、なければ追加
            table_name = 'transactions'
            columns_to_add = {
                'source_wallet': 'TEXT',
                'is_balance_reflected': 'BOOLEAN'
            }
            for column_name, column_type in columns_to_add.items():
                column_exists = await conn.fetchval(f'''
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_catalog.pg_attribute
                        WHERE attrelid = '{table_name}'::regclass
                        AND attname = '{column_name}'
                        AND NOT attisdropped
                    );
                ''')
                if not column_exists:
                    await conn.execute(f'ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type};')
                    logger.info(f"{table_name}テーブルに{column_name}カラムを追加しました。")

            
            # balance_check_stateテーブルを再作成
            await conn.execute('''DROP TABLE IF EXISTS balance_check_state;''')
            await conn.execute('''CREATE TABLE IF NOT EXISTS balance_check_state (
                                user_id BIGINT PRIMARY KEY,
                                state TEXT,
                                input_nushi BIGINT,
                                input_pote BIGINT,
                                input_budget BIGINT,
                                input_savings BIGINT,
                                last_checked_at TIMESTAMP WITH TIME ZONE
                            );''')
            logger.info("家計簿・残高チェック機能のテーブルを初期化しました。")

            await conn.execute('''DROP TABLE IF EXISTS past_activities;''')
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

    async def handle_spend_webhook(self, message: discord.Message):
        """Webhookからの支出記録メッセージを自然言語で処理する"""
        try:
            _, text = message.content.split(":", 1)
            text = text.strip()

            source_wallet_name = None
            category_name = None
            amount = None

            # 利用可能な財布とカテゴリのリスト
            wallets = ["ぽて財布", "ぬし財布", "探検隊予算"]
            categories = ["食費", "日用品", "交通費", "趣味", "交際費", "自己投資", "特別な支出", "その他"]
            
            wallet_pattern = f"({'|'.join(wallets)})"
            category_pattern = f"({'|'.join(categories)})"
            amount_pattern = r"(\d+)"

            # パターン1: 「(財布)で(カテゴリ)に(金額)円」
            match = re.search(rf"{wallet_pattern}で{category_pattern}に{amount_pattern}円", text)
            if match:
                source_wallet_name, category_name, amount_str = match.groups()
                amount = int(amount_str)

            # パターン2: 「(カテゴリ)に(金額)円、(財布)から」
            if not match:
                match = re.search(rf"{category_pattern}に{amount_pattern}円、{wallet_pattern}から", text)
                if match:
                    category_name, amount_str, source_wallet_name = match.groups()
                    amount = int(amount_str)
            
            # パターン3: 財布の指定がない場合 「(カテゴリ)に(金額)円」
            if not match:
                match = re.search(rf"{category_pattern}に{amount_pattern}円", text)
                if match:
                    category_name, amount_str = match.groups()
                    amount = int(amount_str)
                    source_wallet_name = "ぽて財布" # デフォルト

            # パターン4: 「(金額)円を(カテゴリ)として(財布)から」
            if not match:
                match = re.search(rf"{amount_pattern}円を{category_pattern}として{wallet_pattern}から", text)
                if match:
                    amount_str, category_name, source_wallet_name = match.groups()
                    amount = int(amount_str)

            # パターン5: 財布指定なし 「(金額)円を(カテゴリ)として」
            if not match:
                match = re.search(rf"{amount_pattern}円を{category_pattern}として", text)
                if match:
                    amount_str, category_name = match.groups()
                    amount = int(amount_str)
                    source_wallet_name = "ぽて財布" # デフォルト

            if not all([source_wallet_name, category_name, amount]):
                await message.channel.send("うーむ、支出の内容がうまく聞き取れなかった。もう一度試してみてくれ。\\n例: 「ぽて財布で食費に500円」")
                return

            if amount <= 0:
                await message.channel.send("支出額は正の数値を指定しろ！")
                return

            if not Config.OWNER_ID:
                await message.channel.send("エラー: `OWNER_ID`が設定されていません。")
                return
            user_id = Config.OWNER_ID

            async with self.db_pool.acquire() as conn:
                async with conn.transaction():
                    current_balance_record = await conn.fetchrow("SELECT balance FROM user_balances WHERE user_id = $1 AND category = $2", user_id, source_wallet_name)
                    current_balance = current_balance_record['balance'] if current_balance_record else 0

                    if current_balance < amount:
                        await message.channel.send(f"おい隊員！ {source_wallet_name} の残高が足りないぞ！ (現在: {current_balance}円)")
                        return

                    await conn.execute(
                        'UPDATE user_balances SET balance = balance - $1 WHERE user_id = $2 AND category = $3',
                        amount, user_id, source_wallet_name
                    )

                    await conn.execute(
                        "INSERT INTO transactions (user_id, transaction_type, category, amount) VALUES ($1, 'spend', $2, $3);",
                        user_id, category_name, amount
                    )
            
            response_message = (
                f"💸 {category_name} に {amount}円の支出を記録したぞ！ (Webhook経由)\\n"
                f"💳 支払元: {source_wallet_name}\\n"
                f"🫡 {get_captain_quote('spend')}"
            )
            await message.channel.send(response_message)
            await message.add_reaction("✅")

        except Exception as e:
            logger.error(f"Webhook支出記録の処理中にエラーが発生: {e}", exc_info=True)
            await message.channel.send("Webhookの処理中にエラーが発生した。")
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
        self.jst = timezone(timedelta(hours=9))

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info("FinanceCog is ready.")
        self.weekly_balance_check.start()
        self.daily_balance_report.start()

    def cog_unload(self):
        self.weekly_balance_check.cancel()

    @tasks.loop(time=time(20, 0, tzinfo=timezone(timedelta(hours=9))))
    async def weekly_balance_check(self):
        today = datetime.now(self.jst)
        if today.weekday() != 4: return # 4:金曜日
        
        channel_id = self.bot.target_channel_ids[0]
        channel = self.bot.get_channel(channel_id)
        if not channel: return logger.error(f"残高チェック用のチャンネル {channel_id} が見つからん！")

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
                    await channel.send("🚨 毎週の残高チェックの時間だ！これより各財布の残高を順番に確認する。")
                    prompt_sent = True
                
                await conn.execute("INSERT INTO balance_check_state (user_id, state) VALUES ($1, 'waiting_for_balance_ぬし財布') ON CONFLICT (user_id) DO UPDATE SET state = 'waiting_for_balance_ぬし財布', input_nushi=NULL, input_pote=NULL, input_budget=NULL, input_savings=NULL;", user_id)
                try:
                    user = await self.bot.fetch_user(user_id)
                    await user.send("まず【ぬし財布】の現在の残高を半角数字で入力せよ！")
                except (discord.NotFound, discord.Forbidden):
                    await channel.send(f"<@{user_id}>、DMが送信できん！まず【ぬし財布】の現在の残高を半角数字で入力せよ！")

        if prompt_sent: logger.info("残高チェックが必要な隊員への通知を完了した。")

    @app_commands.command(name="check_balance_manual", description="Starts the weekly balance check manually.")
    async def check_balance_manual(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        async with self.bot.db_pool.acquire() as conn:
            await conn.execute("INSERT INTO balance_check_state (user_id, state) VALUES ($1, 'waiting_for_balance_ぬし財布') ON CONFLICT (user_id) DO UPDATE SET state = 'waiting_for_balance_ぬし財布', input_nushi=NULL, input_pote=NULL, input_budget=NULL, input_savings=NULL;", user_id)
        await interaction.response.send_message("🚨 残高チェックを開始する！まず【ぬし財布】の現在の残高を半角数字で入力せよ！", ephemeral=True)

    @app_commands.command(name="reset", description="指定した財布の残高を、指定した金額に再設定するぞ。")
    @app_commands.describe(
        amount="設定する金額",
        wallet="金額を設定する財布"
    )
    @app_commands.choices(wallet=[
        app_commands.Choice(name="ぽて財布", value="ぽて財布"),
        app_commands.Choice(name="ぬし財布", value="ぬし財布"),
        app_commands.Choice(name="探検隊予算", value="探検隊予算"),
        app_commands.Choice(name="貯金", value="貯金"),
    ])
    async def reset_balance(self, interaction: discord.Interaction, amount: int, wallet: app_commands.Choice[str]):
        user_id = interaction.user.id
        target_wallet = wallet.value

        if amount < 0:
            await interaction.response.send_message("おい隊員！リセットする金額は正の数値を指定しろ！", ephemeral=True)
            return
        
        async with self.bot.db_pool.acquire() as conn:
            async with conn.transaction():
                # 指定された財布の残高を更新または挿入
                await conn.execute("""
                    INSERT INTO user_balances (user_id, category, balance)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id, category) DO UPDATE SET balance = $3
                    """, user_id, target_wallet, amount)

                # 取引履歴を記録
                await conn.execute("""
                    INSERT INTO transactions (user_id, transaction_type, category, amount)
                    VALUES ($1, 'reset', $2, $3)
                    """, user_id, target_wallet, amount)
                
                # 残高チェックの状態もリセット
                await conn.execute("UPDATE balance_check_state SET state = NULL, input_nushi=NULL, input_pote=NULL, input_budget=NULL, input_savings=NULL, last_checked_at = NULL WHERE user_id = $1", user_id)
        
        await interaction.response.send_message(f"よし！ **{target_wallet}** の残高を **{amount}** 円に再設定した。")

    @app_commands.command(name="salary", description="給料を受け取り、ルールに基づいて各財布に自動で振り分けるぞ。")
    @app_commands.describe(amount="受け取った給料の総額")
    async def salary(self, interaction: discord.Interaction, amount: int):
        if amount <= 0:
            await interaction.response.send_message("おい隊員！給料は正の数値を指定しろ！", ephemeral=True)
            return

        user_id = interaction.user.id

        # 新しいルールに基づいて金額を振り分け
        nushi_wallet_amount = amount // 2
        pote_wallet_amount = 0
        savings_amount = (amount * 3) // 10
        expedition_budget_amount = (amount * 2) // 10
        
        # 残りを貯金に加算
        remainder = amount - nushi_wallet_amount - pote_wallet_amount - savings_amount - expedition_budget_amount
        savings_amount += remainder

        async with self.bot.db_pool.acquire() as conn:
            async with conn.transaction():
                # 各カテゴリの残高を更新
                for category, cat_amount in [
                    ("ぽて財布", pote_wallet_amount),
                    ("ぬし財布", nushi_wallet_amount),
                    ("貯金", savings_amount),
                    ("探検隊予算", expedition_budget_amount)
                ]:
                    if cat_amount > 0:
                        await conn.execute("""
                            INSERT INTO user_balances (user_id, category, balance)
                            VALUES ($1, $2, $3)
                            ON CONFLICT (user_id, category) DO UPDATE
                            SET balance = user_balances.balance + $3;
                            """, user_id, category, cat_amount)

                # 取引履歴を記録
                await conn.execute("""
                    INSERT INTO transactions (user_id, transaction_type, category, amount)
                    VALUES ($1, 'salary', '給与収入', $2);
                    """, user_id, amount)

        message = (
            f"💰 給料 {amount}円を受け取り、各財布に振り分けたぞ！\n"
            f"💳 ぽて財布: +{pote_wallet_amount}円\n"
            f"💳 ぬし財布: +{nushi_wallet_amount}円\n"
            f"🏦 貯金: +{savings_amount}円\n"
            f"🧳 探検隊予算: +{expedition_budget_amount}円\n"
            f"🫡 {get_captain_quote('salary')}"
        )
        await interaction.response.send_message(message)

    @app_commands.command(name="spend", description="支出を記録するぞ。支払元を指定しない場合は「ぽて財布」から引かれる。")
    @app_commands.describe(
        amount="支出した金額",
        category="支出のカテゴリ",
        from_wallet="どの財布から支払うか",
        reflect_balance="【任意】残高に反映させるか？デフォルトは「はい」。過去の支出を記録する際は「いいえ」を選択しろ。",
        date="【任意】支出日をYYYY-MM-DD形式で指定。未指定の場合は本日日付となる。"
    )
    @app_commands.choices(
        category=[
            app_commands.Choice(name="食費", value="食費"),
            app_commands.Choice(name="日用品", value="日用品"),
            app_commands.Choice(name="交通費", value="交通費"),
            app_commands.Choice(name="趣味", value="趣味"),
            app_commands.Choice(name="交際費", value="交際費"),
            app_commands.Choice(name="自己投資", value="自己投資"),
            app_commands.Choice(name="特別な支出", value="特別な支出"),
            app_commands.Choice(name="その他", value="その他"),
        ],
        from_wallet=[
            app_commands.Choice(name="ぽて財布", value="ぽて財布"),
            app_commands.Choice(name="ぬし財布", value="ぬし財布"),
            app_commands.Choice(name="探検隊予算", value="探検隊予算"),
        ],
        reflect_balance=[
            app_commands.Choice(name="はい", value=1),
            app_commands.Choice(name="いいえ", value=0),
        ]
    )
    async def spend(self, interaction: discord.Interaction, amount: int, category: app_commands.Choice[str], from_wallet: app_commands.Choice[str] = None, reflect_balance: app_commands.Choice[int] = None, date: str = None):
        if amount <= 0:
            await interaction.response.send_message("おい隊員！支出額は正の数値を指定しろ！", ephemeral=True)
            return

        user_id = interaction.user.id
        category_name = category.value
        source_wallet_name = from_wallet.value if from_wallet else "ぽて財布"
        
        should_reflect = reflect_balance.value == 1 if reflect_balance is not None else True

        # 取引日時を決定
        transaction_time = datetime.now(self.jst)
        if date:
            try:
                # YYYY-MM-DD形式を想定し、時刻は実行時のものを利用
                now_time = datetime.now(self.jst).time()
                transaction_time = datetime.strptime(date, "%Y-%m-%d").replace(hour=now_time.hour, minute=now_time.minute, second=now_time.second, tzinfo=self.jst)
            except ValueError:
                await interaction.response.send_message("日付の形式が正しくないようだ。`YYYY-MM-DD`の形式で入力してくれ。", ephemeral=True)
                return

        async with self.bot.db_pool.acquire() as conn:
            async with conn.transaction():
                if should_reflect:
                    current_balance_record = await conn.fetchrow("SELECT balance FROM user_balances WHERE user_id = $1 AND category = $2", user_id, source_wallet_name)
                    current_balance = current_balance_record['balance'] if current_balance_record else 0

                    if current_balance < amount:
                        await interaction.response.send_message(f"おい隊員！ {source_wallet_name} の残高が足りないぞ！ (現在: {current_balance}円)", ephemeral=True)
                        return

                    await conn.execute('''
                        UPDATE user_balances SET balance = balance - $1 WHERE user_id = $2 AND category = $3
                        ''', amount, user_id, source_wallet_name)

                # 取引履歴を記録
                await conn.execute('''
                    INSERT INTO transactions (user_id, transaction_type, category, amount, created_at, source_wallet, is_balance_reflected)
                    VALUES ($1, 'spend', $2, $3, $4, $5, $6);
                    ''', user_id, category_name, amount, transaction_time, source_wallet_name, should_reflect)

        message = (
            f"💸 {category_name} に {amount}円の支出を記録したぞ！\n"
            f"🗓️ 日付: {transaction_time.strftime('%Y-%m-%d')}\n"
            f"💳 支払元: {source_wallet_name}\n"
        )
        if not should_reflect:
            message += "過去の記録として登録したため、残高は変更されていない。\n"
        
        message += f"🫡 {get_captain_quote('spend')}"

        await interaction.response.send_message(message)

    @app_commands.command(name="transfer", description="財布から別の財布へ資金を移動するぞ。")
    @app_commands.describe(
        amount="移動する金額",
        from_wallet="移動元の財布",
        to_wallet="移動先の財布"
    )
    @app_commands.choices(
        from_wallet=[
            app_commands.Choice(name="ぽて財布", value="ぽて財布"),
            app_commands.Choice(name="ぬし財布", value="ぬし財布"),
            app_commands.Choice(name="探検隊予算", value="探検隊予算"),
            app_commands.Choice(name="貯金", value="貯金"),
        ],
        to_wallet=[
            app_commands.Choice(name="ぽて財布", value="ぽて財布"),
            app_commands.Choice(name="ぬし財布", value="ぬし財布"),
            app_commands.Choice(name="探検隊予算", value="探検隊予算"),
            app_commands.Choice(name="貯金", value="貯金"),
        ]
    )
    async def transfer(self, interaction: discord.Interaction, amount: int, from_wallet: app_commands.Choice[str], to_wallet: app_commands.Choice[str]):
        if amount <= 0:
            await interaction.response.send_message("おい隊員！移動する金額は正の数値を指定しろ！", ephemeral=True)
            return

        user_id = interaction.user.id
        source_wallet = from_wallet.value
        destination_wallet = to_wallet.value

        if source_wallet == destination_wallet:
            await interaction.response.send_message("おい隊員！同じ財布の間では資金を移動できん！", ephemeral=True)
            return

        async with self.bot.db_pool.acquire() as conn:
            async with conn.transaction():
                # 移動元の残高を取得
                source_balance_record = await conn.fetchrow("SELECT balance FROM user_balances WHERE user_id = $1 AND category = $2", user_id, source_wallet)
                source_balance = source_balance_record['balance'] if source_balance_record else 0

                if source_balance < amount:
                    await interaction.response.send_message(f"おい隊員！ {source_wallet} の残高が足りないぞ！ (現在: {source_balance}円)", ephemeral=True)
                    return

                # 移動元の残高を減らす
                await conn.execute("UPDATE user_balances SET balance = balance - $1 WHERE user_id = $2 AND category = $3", amount, user_id, source_wallet)
                
                # 移動先の残高を増やす
                await conn.execute("""
                    INSERT INTO user_balances (user_id, category, balance)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id, category) DO UPDATE
                    SET balance = user_balances.balance + $3;
                    """, user_id, destination_wallet, amount)

                # 取引履歴を記録
                await conn.execute("""
                    INSERT INTO transactions (user_id, transaction_type, category, amount)
                    VALUES ($1, 'transfer', $2, $3);
                    """, user_id, f"{source_wallet}から{destination_wallet}へ", amount)

        message = (
            f"🔄 {source_wallet} から {destination_wallet} へ {amount}円を移動したぞ。\n"
            f"🫡 {get_captain_quote('balance')}"
        )
        await interaction.response.send_message(message)

    @app_commands.command(name="balance", description="すべての財布の現在の残高を一覧で表示するぞ。")
    async def balance(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        
        async with self.bot.db_pool.acquire() as conn:
            records = await conn.fetch("SELECT category, balance FROM user_balances WHERE user_id = $1 ORDER BY category", user_id)

        if not records:
            await interaction.response.send_message("まだ財布の残高記録がないようだ。まずは `/salary` などで収入を記録しよう！", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"{interaction.user.display_name} の財産状況",
            color=discord.Color.blue(),
            timestamp=datetime.now(self.jst)
        )

        total_balance = 0
        wallet_order = ["ぬし財布", "ぽて財布", "探検隊予算", "貯金"] # 表示順を定義
        
        # 順序通りに表示するために一度辞書化
        balances = {record['category']: record['balance'] for record in records}
        
        # 定義した順序で財布情報を追加
        for wallet_name in wallet_order:
            if wallet_name in balances:
                balance = balances[wallet_name]
                embed.add_field(name=wallet_name, value=f"{balance:,} 円", inline=False)
                total_balance += balance
            
        # 順序リストに含まれない財布があった場合も表示（念のため）
        other_wallets = {k: v for k, v in balances.items() if k not in wallet_order}
        for wallet_name, balance in other_wallets.items():
            embed.add_field(name=wallet_name, value=f"{balance:,} 円", inline=False)
            total_balance += balance

        embed.set_footer(text=f"合計資産: {total_balance:,} 円")
        
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="history", description="最近のお金の動きの履歴を表示するぞ。")
    @app_commands.describe(limit="表示する履歴の件数（1〜25件）")
    async def history(self, interaction: discord.Interaction, limit: app_commands.Range[int, 1, 25] = 10):
        user_id = interaction.user.id
        
        async with self.bot.db_pool.acquire() as conn:
            records = await conn.fetch(
                "SELECT id, transaction_type, category, amount, created_at, source_wallet, is_balance_reflected FROM transactions WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2",
                user_id, limit
            )

        if not records:
            await interaction.response.send_message("まだ取引履歴がないようだ。", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"最近の取引履歴 ({len(records)}件)",
            color=discord.Color.green(),
            timestamp=datetime.now(self.jst)
        )

        description_lines = []
        for record in records:
            dt_jst = record['created_at'].astimezone(self.jst)
            time_str = dt_jst.strftime('%Y/%m/%d %H:%M')
            
            tx_type = record['transaction_type']
            category = record['category']
            amount = record['amount']
            tx_id = record['id']

            if tx_type == 'salary':
                emoji = '💰'
                details = f"給与収入: **+{amount:,}円**"
            elif tx_type == 'spend':
                emoji = '💸'
                source_wallet_info = f" (支払元: {record['source_wallet']})" if record['source_wallet'] else ""
                balance_reflected_info = " (残高反映なし)" if record['is_balance_reflected'] == False else ""
                details = f"支出 ({category}): **-{amount:,}円**{source_wallet_info}{balance_reflected_info}"
            elif tx_type == 'transfer':
                emoji = '🔄'
                details = f"振替 ({category}): **{amount:,}円**"
            elif tx_type == 'reset':
                emoji = '🔧'
                details = f"残高リセット ({category}): **{amount:,}円** に設定"
            else:
                emoji = '🧾'
                details = f"{tx_type} ({category}): {amount:,}円"

            description_lines.append(f"`{time_str}` `ID:{tx_id}` {emoji} {details}")

        embed.description = "\n".join(description_lines)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="edit_spend", description="指定したIDの支出記録を修正するぞ。")
    @app_commands.describe(
        transaction_id="修正する支出の取引ID (`/history`で確認しろ)",
        amount="【任意】新しい支出額",
        category="【任意】新しい支出カテゴリ",
        from_wallet="【任意】新しい支払元の財布",
        date="【任意】新しい支出日 (YYYY-MM-DD)",
        reflect_balance="【任意】残高への反映をどうするか"
    )
    @app_commands.choices(
        category=[
            app_commands.Choice(name="食費", value="食費"),
            app_commands.Choice(name="日用品", value="日用品"),
            app_commands.Choice(name="交通費", value="交通費"),
            app_commands.Choice(name="趣味", value="趣味"),
            app_commands.Choice(name="交際費", value="交際費"),
            app_commands.Choice(name="自己投資", value="自己投資"),
            app_commands.Choice(name="特別な支出", value="特別な支出"),
            app_commands.Choice(name="その他", value="その他"),
        ],
        from_wallet=[
            app_commands.Choice(name="ぽて財布", value="ぽて財布"),
            app_commands.Choice(name="ぬし財布", value="ぬし財布"),
            app_commands.Choice(name="探検隊予算", value="探検隊予算"),
        ],
        reflect_balance=[
            app_commands.Choice(name="はい", value=1),
            app_commands.Choice(name="いいえ", value=0),
        ]
    )
    async def edit_spend(self, interaction: discord.Interaction,
                         transaction_id: int,
                         amount: int = None,
                         category: app_commands.Choice[str] = None,
                         from_wallet: app_commands.Choice[str] = None,
                         date: str = None,
                         reflect_balance: app_commands.Choice[int] = None):
        
        await interaction.response.defer(ephemeral=False)
        user_id = interaction.user.id

        try:
            async with self.bot.db_pool.acquire() as conn:
                async with conn.transaction():
                    # 1. 元の取引情報を取得
                    old_tx = await conn.fetchrow("SELECT * FROM transactions WHERE id = $1 AND user_id = $2 AND transaction_type = 'spend'", transaction_id, user_id)

                    if not old_tx:
                        raise ValueError("指定されたIDの支出取引が見つからないか、権限がないぞ。")

                    # 2. 新しい取引情報を準備
                    new_amount = amount if amount is not None else old_tx['amount']
                    if new_amount <= 0:
                        raise ValueError("支出額は正の数値を指定しろ！")

                    new_category = category.value if category is not None else old_tx['category']
                    new_wallet = from_wallet.value if from_wallet is not None else old_tx['source_wallet']
                    
                    if reflect_balance is None:
                        new_reflect_balance = old_tx['is_balance_reflected']
                    else:
                        new_reflect_balance = True if reflect_balance.value == 1 else False

                    new_time = old_tx['created_at']
                    if date:
                        try:
                            original_time = old_tx['created_at'].astimezone(self.jst).time()
                            new_time = datetime.strptime(date, "%Y-%m-%d").replace(hour=original_time.hour, minute=original_time.minute, second=original_time.second, tzinfo=self.jst)
                        except ValueError:
                            raise ValueError("日付の形式が正しくないようだ。`YYYY-MM-DD`の形式で入力してくれ。")

                    # 3. 残高の巻き戻し
                    if old_tx['is_balance_reflected'] and old_tx['source_wallet']:
                         await conn.execute("UPDATE user_balances SET balance = balance + $1 WHERE user_id = $2 AND category = $3", old_tx['amount'], user_id, old_tx['source_wallet'])

                    # 4. 残高の再適用
                    if new_reflect_balance and new_wallet:
                        current_balance_record = await conn.fetchrow("SELECT balance FROM user_balances WHERE user_id = $1 AND category = $2", user_id, new_wallet)
                        current_balance = current_balance_record['balance'] if current_balance_record else 0
                        if current_balance < new_amount:
                            raise ValueError(f"新しい支払元 {new_wallet} の残高が足りない。")

                        await conn.execute("UPDATE user_balances SET balance = balance - $1 WHERE user_id = $2 AND category = $3", new_amount, user_id, new_wallet)

                    # 5. 取引記録の更新
                    await conn.execute("""
                        UPDATE transactions
                        SET amount = $1, category = $2, source_wallet = $3, created_at = $4, is_balance_reflected = $5
                        WHERE id = $6
                    """, new_amount, new_category, new_wallet, new_time, new_reflect_balance, transaction_id)
            
            await interaction.followup.send(f"✅ 取引ID: {transaction_id} の支出記録を修正したぞ！")

        except ValueError as e:
            await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
        except Exception as e:
            logger.error(f"取引修正(ID: {transaction_id})中に予期せぬエラー: {e}", exc_info=True)
            await interaction.followup.send("予期せぬエラーにより、修正に失敗した。変更は取り消された。", ephemeral=True)

    @tasks.loop(time=time(12, 0, tzinfo=timezone(timedelta(hours=9))))
    async def daily_balance_report(self):
        logger.info("正午の残高レポートタスクを開始する。")
        
        try:
            channel_id = self.bot.target_channel_ids[0]
            channel = self.bot.get_channel(channel_id)
            if not channel:
                logger.error(f"残高レポート用のチャンネル {channel_id} が見つからん！")
                return
        except (IndexError, AttributeError):
            logger.error("残高レポート用のチャンネルIDが設定されていない！")
            return

        async with self.bot.db_pool.acquire() as conn:
            user_records = await conn.fetch("SELECT DISTINCT user_id FROM user_balances")
            
            if not user_records:
                logger.info("残高レポート対象のユーザーが見つからなかった。")
                return

            for record in user_records:
                user_id = record['user_id']
                
                try:
                    user = await self.bot.fetch_user(user_id)
                except discord.NotFound:
                    logger.warning(f"ユーザーID {user_id} が見つからなかったため、残高レポートをスキップする。")
                    continue

                balance_records = await conn.fetch("SELECT category, balance FROM user_balances WHERE user_id = $1 ORDER BY category", user_id)

                if not balance_records:
                    continue

                embed = discord.Embed(
                    title=f"正午の財産状況レポートだ！",
                    color=discord.Color.blue(),
                    timestamp=datetime.now(self.jst)
                )

                total_balance = 0
                wallet_order = ["ぬし財布", "ぽて財布", "探検隊予算", "貯金"]
                balances = {r['category']: r['balance'] for r in balance_records}
                
                for wallet_name in wallet_order:
                    if wallet_name in balances:
                        balance = balances[wallet_name]
                        embed.add_field(name=wallet_name, value=f"{balance:,} 円", inline=False)
                        total_balance += balance
                
                other_wallets = {k: v for k, v in balances.items() if k not in wallet_order}
                for wallet_name, balance in other_wallets.items():
                    embed.add_field(name=wallet_name, value=f"{balance:,} 円", inline=False)
                    total_balance += balance

                embed.set_footer(text=f"合計資産: {total_balance:,} 円")
                
                try:
                    await user.send(embed=embed)
                    logger.info(f"ユーザー {user.display_name} ({user_id}) に残高レポートをDMで送信した。")
                except discord.Forbidden:
                    logger.warning(f"ユーザー {user.display_name} ({user_id}) へのDM送信に失敗。チャンネルに投稿する。")
                    await channel.send(content=f"<@{user_id}>、DMが送れなかったため、ここに正午の財産状況を報告する！", embed=embed)
                except Exception as e:
                    logger.error(f"ユーザー {user.display_name} ({user_id}) への残高レポート送信中に予期せぬエラーが発生: {e}")

        logger.info("正午の残高レポートタスクを完了した。")
