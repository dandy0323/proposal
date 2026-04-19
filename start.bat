@echo off
chcp 65001 > nul
echo AI Design Automation - セットアップ＆起動
echo ========================================

cd /d %~dp0

REM .envファイルが存在しない場合は作成
if not exist .env (
    copy .env.example .env > nul
    echo [!] .env ファイルを作成しました。
    echo     APIキーを設定してから再度実行してください。
    echo     ファイル場所: %~dp0.env
    pause
    start notepad .env
    exit /b
)

REM APIキーが未設定の場合は警告
findstr /c:"your_anthropic_api_key_here" .env > nul
if %errorlevel% == 0 (
    echo [!] .env の ANTHROPIC_API_KEY が未設定です。
    start notepad .env
    pause
    exit /b
)

echo [1/2] 依存パッケージをインストール中...
pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo [エラー] pip install に失敗しました。
    pause
    exit /b
)

echo [2/2] サーバーを起動します...
echo.
echo ブラウザで以下のURLを開いてください:
echo   http://localhost:8000
echo.
echo サーバーを停止するには Ctrl+C を押してください。
echo.
python -m uvicorn main:app --reload --port 8000
pause
