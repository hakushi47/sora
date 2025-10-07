@echo off

rem Set console to UTF-8
chcp 65001

echo Discord キーワード収集Bot を開始します...
echo.

REM 環境変数ファイルの存在確認
if not exist .env (
    echo エラー: .env ファイルが見つかりません
    echo README.md を参照して .env ファイルを作成してください
    pause
    exit /b 1
)

REM PowerShellを使ってUTF-8でログを記録しつつ、コンソールにも表示
powershell -Command "python -X utf8 main.py --schedule 2>&1 | Tee-Object -FilePath 'run_out.txt' -Encoding 'utf8'"

pause
