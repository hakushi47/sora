# Discord 備品管理Bot

Discord上で備品の保管場所や在庫を管理するためのBotです。

## 機能

- 保管場所の登録・管理
- 備品の登録・管理
- 備品の場所を検索
- 保管場所の中身を一覧表示

## セットアップ

### 1. 依存関係のインストール

```bash
pip install -r requirements.txt
```

### 2. Discord Botの作成と設定

1. [Discord Developer Portal](https://discord.com/developers/applications)にアクセス
2. 「New Application」をクリックしてアプリケーションを作成
3. 「Bot」セクションに移動
4. 「Add Bot」をクリックしてBotを作成
5. 以下の権限を設定：
   - `Read Messages` - メッセージを読み取り
   - `Send Messages` - メッセージを送信
   - `Read Message History` - メッセージ履歴を読み取り
   - `View Channels` - チャンネルを表示
6. 「Copy」をクリックしてBot Tokenをコピー
7. 「OAuth2」→「URL Generator」でBotをサーバーに招待

### 3. 環境変数の設定

`.env`ファイルを作成し、以下の内容を設定します。

```env
# Discord Bot設定
DISCORD_BOT_TOKEN=your-bot-token-here
TARGET_CHANNEL_ID=1234567890123456789 # Botが反応するチャンネルID
GUILD_ID=1234567890123456789

# データベース設定 (PostgreSQL)
DATABASE_URL=postgresql://user:password@host:port/database
```

## 使用方法

Botに対してメンションは不要です。設定したチャンネル内で以下のメッセージを送信することで、各機能を利用できます。

### 備品の登録・管理

#### 新しい保管場所の追加
- **あなた:** `新しい保管場所を追加したい`
- **Bot:** `いいですね！保管場所の名前は？`
- **あなた:** `（保管場所名）`
- **Bot:** `「（保管場所名）」を登録しました！`

#### 備品の登録
- **あなた:** `（備品名）を登録したい`
- **Bot:** `どの保管場所にいれる？`
- **あなた:** `（保管場所名）`
- **Bot:** `「（備品名）」を「（保管場所名）」に登録しました！`

### 備品の検索

#### 備品の場所を検索
- **あなた:** `（備品名）はどこ？`
- **Bot:** `「（備品名）」は「（保管場所名）」にあるよ！`

#### 保管場所の中身を確認
- **あなた:** `（保管場所名）の中身は？`
- **Bot:** `「（備品名1）」「（備品名2）」が入っているよ！`

### Botの起動

```bash
python main.py --monitor
```
Botが常時起動し、メッセージに反応するようになります。
停止はウィンドウを閉じるか `Ctrl+C` を入力します。

## ファイル構成

```
sora/
├── main.py              # メインアプリケーション
├── config.py            # 設定管理
├── discord_client.py    # Discord API クライアント
├── scheduler.py         # スケジューラー（現在未使用）
├── requirements.txt     # 依存関係
├── README.md           # このファイル
└── .env                # 環境変数（要作成）
```

## 設定項目

| 項目 | 説明 |
|------|------|
| `DISCORD_BOT_TOKEN` | Discord BotのToken |
| `TARGET_CHANNEL_ID` | Botが反応するチャンネルのID |
| `GUILD_ID` | Botが動作するサーバーのID |
| `DATABASE_URL` | PostgreSQLデータベースの接続URL |

## ログ

ログは`discord_bot.log`ファイルに出力されます。

## トラブルシューティング

### よくある問題

1. **Botが反応しない**
   - `.env`ファイルの `TARGET_CHANNEL_ID` が正しいか確認してください。
   - BotがDiscordサーバーに正しく招待されているか確認してください。
   - Botに必要な権限（メッセージの読み取り・書き込み等）が付与されているか確認してください。

2. **データベースに接続できない**
   - `.env`ファイルの `DATABASE_URL` が正しいか確認してください。
   - データベースが起動しており、外部からの接続を許可しているか確認してください。

## Render デプロイ手順

### 1. GitHubにリポジトリをプッシュ
（省略）

### 2. Renderでサービス作成
1. [Render](https://render.com)にアクセスし、新しいWeb Serviceを作成します。
2. GitHubリポジトリを選択します。
3. 以下の設定を行います。
   - **Name**: `discord-inventory-bot` （または好きな名前）
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python main.py --monitor`

### 3. データベースの作成と環境変数設定
1. Renderで新しくPostgreSQLデータベースを作成します。
2. 作成したデータベースの「Internal Connection String」をコピーします。
3. Web Serviceの環境変数に、以下のキーと値を設定します。
   - `DISCORD_BOT_TOKEN`: あなたのDiscord Bot Token
   - `TARGET_CHANNEL_ID`: Botを動作させるチャンネルID
   - `GUILD_ID`: Botを動作させるサーバーID
   - `DATABASE_URL`: （2でコピーしたデータベースの接続文字列）

### 4. デプロイ
「Create Web Service」をクリックしてデプロイを開始します。


### 4. デプロイ
「Create Web Service」をクリックしてデプロイ開始

### 注意事項
- Renderの無料プランでは15分でスリープしますが、メッセージが来ると自動復帰します

## ライセンス

MIT License

## 貢献

プルリクエストやイシューの報告を歓迎します。
