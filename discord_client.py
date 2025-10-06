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
            logger.info(">>>>>> 新バージョンのコードが正常に起動しました！<<<<<<")
            logger.info(f'{self.user} として監視を開始')
            await self.init_db() # DB初期化
            logger.info("データベースの初期化が完了しました。")

            await self.add_cog(FinanceCog(self))
            logger.info("FinanceCogをロードしました。")

            await self.tree.sync()
            logger.info("コマンドツリーを同期しました。Botの準備完了です！")

        except Exception as e:
            logger.error("on_readyで致命的なエラーが発生しました。", exc_info=True)

    async def on_message(self, message: discord.Message):
        if message.author.id == self.user.id or not message.guild:
            return

        ctx = await self.get_context(message)
        if ctx.valid:
            await self.invoke(ctx)
            return

        user_id = message.author.id
        content = message.content.strip()

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
        self.weekly_balance_check.start()

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

    @app_commands.command(name="check_balance_manual", description="週次の残高チェックを手動で開始するぞ！")
    async def check_balance_manual(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        async with self.bot.db_pool.acquire() as conn:
            await conn.execute("INSERT INTO balance_check_state (user_id, state) VALUES ($1, 'waiting_for_balance_ぬし財布') ON CONFLICT (user_id) DO UPDATE SET state = 'waiting_for_balance_ぬし財布', input_nushi=NULL, input_pote=NULL, input_budget=NULL, input_savings=NULL;", user_id)
        await interaction.response.send_message("🚨 残高チェックを開始する！まず【ぬし財布】の現在の残高を半角数字で入力せよ！", ephemeral=True)

    @commands.command(name="リセット")
    async def reset_balance(self, ctx: commands.Context, amount: int):
        user_id = ctx.author.id
        if amount < 0:
            await ctx.send("おい隊員！リセットする金額は正の数値を指定しろ！")
            return
        
        async with self.bot.db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM user_balances WHERE user_id = $1 AND category IN ('ぬし財布', 'ぽて財布', '探検隊予算', '貯金')", user_id)
                await conn.execute("INSERT INTO user_balances (user_id, category, balance) VALUES ($1, 'ぽて財布', $2)", user_id, amount)
                await conn.execute("UPDATE balance_check_state SET state = NULL, input_nushi=NULL, input_pote=NULL, input_budget=NULL, input_savings=NULL, last_checked_at = NULL WHERE user_id = $1", user_id)
        
        await ctx.send(f"よし！残高をリセットし、**ぽて財布**を **{amount}** 円に設定した。")
