# Discord 備品管理Bot

Discord上で備品の保管場所や在庫を管理するためのBotです。

## 機能

- 備品の登録・管理
- 備品の場所を検索
- 保管場所の中身を一覧表示
- キーワードへの自動リアクション

## セットアップ

### 1. 依存関係のインストール

```bash
pip install -r requirements.txt
```

### 2. Discord Botの作成と設定

（省略）

### 3. 環境変数の設定

`.env`ファイルを作成し、以下の内容を設定します。

```env
# Discord Bot設定
DISCORD_BOT_TOKEN=your-bot-token-here
TARGET_CHANNEL_ID=1234567890123456789 # Botが反応するチャンネルID
GUILD_ID=1234567890123456789

# データベース設定 (PostgreSQL)
DATABASE_URL=postgresql://user:password@host:port/database

# キーワードリアクション設定
KEYWORD_REACTIONS=なう:🕒,わず:✅,うぃる:🗓️
```

## 使用方法

### キーワードへの自動リアクション

メッセージに特定のキーワードが含まれていると、Botが自動でリアクションを付けます。
キーワードとリアクションの組み合わせは `KEYWORD_REACTIONS` 環境変数でカスタマイズできます。

**デフォルト設定の例:**
- `なう` を含むメッセージ → 🕒
- `わず` を含むメッセージ → ✅
- `うぃる` を含むメッセージ → 🗓️

### 備品の登録・管理

Botに対してメンションは不要です。設定したチャンネル内で以下のメッセージを送信することで、各機能を利用できます。

（省略）

## 設定項目

| 項目 | 説明 |
|------|------|
| `DISCORD_BOT_TOKEN` | Discord BotのToken |
| `TARGET_CHANNEL_ID` | Botが反応するチャンネルのID |
| `GUILD_ID` | Botが動作するサーバーのID |
| `DATABASE_URL` | PostgreSQLデータベースの接続URL |
| `KEYWORD_REACTIONS` | `キーワード:リアクション` のペアをカンマ区切りで指定 |

## ログ

（省略）

## Render デプロイ手順

（省略）

### 3. データベースの作成と環境変数設定

（省略）

3. Web Serviceの環境変数に、以下のキーと値を設定します。
   - `DISCORD_BOT_TOKEN`: あなたのDiscord Bot Token
   - `TARGET_CHANNEL_ID`: Botを動作させるチャンネルID
   - `GUILD_ID`: Botを動作させるサーバーID
   - `DATABASE_URL`: （2でコピーしたデータベースの接続文字列）
   - `KEYWORD_REACTIONS`: `なう:🕒,わず:✅` など（任意）

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
