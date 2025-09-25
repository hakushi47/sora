# Discord キーワード収集Bot

特定のキーワード（「なう」「わず」）を含むDiscordメッセージを収集し、毎日定時にDiscordに投稿するBotです。

## 機能

- 指定されたキーワードを含むDiscordメッセージの自動収集
- 毎日定時（デフォルト: 09:00）にサマリーをDiscordに投稿
- 複数チャンネルからの同時収集

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

`.env`ファイルを作成し、以下の内容を設定：

```env
# Discord Bot設定
DISCORD_BOT_TOKEN=your-bot-token-here
TARGET_CHANNEL_ID=1234567890123456789
GUILD_ID=1234567890123456789


# キーワード設定
KEYWORDS=なう,わず

# スケジュール設定（24時間形式）
SCHEDULE_TIME=09:00
```

## 使用方法

### スケジューラーモード（推奨）

```bash
python main.py --schedule
```

毎日指定時刻に自動実行されます。

### 一回限りの実行（テスト用）

```bash
python main.py --once
```

### デバッグモード
### 常時監視モード（新機能）

```bash
python main.py --monitor
```

- メッセージを常時監視し、`なう`/`わず`/`うぃる` 等のキーワードを含む投稿を検出すると、対象メッセージにリアクションを付与します。（例: `なう` → 🕒、`わず` → ✅、`うぃる` → 🗓️）

停止はウィンドウを閉じるか Ctrl+C。

```bash
python main.py --debug
```

## ファイル構成

```
sora/
├── main.py              # メインアプリケーション
├── config.py            # 設定管理
├── discord_client.py    # Discord API クライアント
├── scheduler.py         # スケジューラー
├── requirements.txt     # 依存関係
├── README.md           # このファイル
└── .env                # 環境変数（要作成）
```

## 設定項目

| 項目 | 説明 | デフォルト値 |
|------|------|-------------|
| `DISCORD_BOT_TOKEN` | Discord Bot Token | 必須 |
| `TARGET_CHANNEL_ID` | サマリー投稿先チャンネルID | 必須 |
| `GUILD_ID` | サーバーID（オプション） | なし |
| `KEYWORDS` | 収集対象キーワード（カンマ区切り） | `なう,わず` |
| `SCHEDULE_TIME` | 実行時刻（24時間形式） | `09:00` |

## ログ

ログは`discord_bot.log`ファイルに出力されます。デバッグ情報が必要な場合は`--debug`オプションを使用してください。

## トラブルシューティング

### よくある問題

1. **Bot Tokenが無効**
   - Discord Developer Portalの設定を確認
   - トークンの権限を確認

2. **チャンネルにアクセスできない**
   - Botを対象サーバーに招待
   - チャンネルの権限設定を確認


4. **メッセージが収集されない**
   - キーワードの設定を確認
   - Botがサーバーに参加しているか確認
   - チャンネルIDが正しいか確認

## Render デプロイ手順

### 1. GitHubにリポジトリをプッシュ
```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/yourusername/your-repo.git
git push -u origin main
```

### 2. Renderでサービス作成
1. [Render](https://render.com)にアクセス
2. 「New +」→「Web Service」
3. GitHubリポジトリを選択
4. 設定：
   - **Name**: `discord-keyword-monitor`
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python main.py --monitor`

### 3. 環境変数設定
Renderのダッシュボードで以下を設定：
- `DISCORD_BOT_TOKEN`: あなたのDiscord Bot Token
- `TARGET_CHANNEL_ID`: 1327475246680244415
- `GUILD_ID`: 1327472468050448404
- `KEYWORDS`: `なう,わず,うぃる`
- `SCHEDULE_TIME`: `09:00`

### 4. デプロイ
「Create Web Service」をクリックしてデプロイ開始

### 注意事項
- Renderの無料プランでは15分でスリープしますが、メッセージが来ると自動復帰します

## ライセンス

MIT License

