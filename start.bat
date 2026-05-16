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

REM 最新コードを自動取得
echo [0/2] 最新コードを取得中...
git pull origin claude/ai-design-automation-app-xju0x
if %errorlevel% neq 0 (
    echo [警告] git pull に失敗しましたが、起動を続行します。
)

echo [1/2] 依存パッケージをインストール中...
pip install -r requirements.txt -q 2>nul
if %errorlevel% neq 0 (
    echo [警告] pip install でエラーが発生しましたが、起動を続行します。
    echo        （Anaconda環境のパッケージバージョン問題のため無視可能です）
)

echo [2/2] サーバーを起動します...
echo.
echo ブラウザで以下のURLを開いてください:
echo   http://localhost:8000
echo.
echo サーバーを停止するには Ctrl+C を押してください。
echo.

REM 自動pull（30秒ごとにバックグラウンドで実行・ウィンドウなし）
start "" powershell -WindowStyle Hidden -Command "while($true){Start-Sleep 30; git -C '%~dp0.' fetch origin claude/ai-design-automation-app-xju0x 2>$null; git -C '%~dp0.' pull origin claude/ai-design-automation-app-xju0x 2>$null}"

REM ngrokが存在する場合はスマホ用トンネルを自動起動
where ngrok >nul 2>&1
if %errorlevel% == 0 (
    echo [ngrok] スマホ用トンネルを起動中...
    start "ngrok" /min ngrok http 8000
    timeout /t 3 /nobreak > nul
    echo [ngrok] 起動しました。別ウィンドウのngrok画面に表示されるURLをスマホで開いてください。
    echo         例: https://xxxx-xxx-xxx-xxx-xxx.ngrok-free.app
    echo.
) else (
    echo [ngrok] ヒント: ngrokをインストールするとスマホからもアクセスできます。
    echo         詳細は README_SMARTPHONE.md を参照してください。
    echo.
)

python -m uvicorn main:app --reload --port 8000
pause
