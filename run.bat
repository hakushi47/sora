@echo off
echo Discord キーワード収集Bot を開始します...
echo.

REM 環境変数ファイルの存在確認
if not exist .env (
    echo エラー: .env ファイルが見つかりません
    echo README.md を参照して .env ファイルを作成してください
    pause
    exit /b 1
)

REM Pythonの実行
python main.py --schedule

pause
