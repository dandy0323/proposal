@echo off
chcp 65001 > nul
cd /d %~dp0
:loop
timeout /t 30 /nobreak > nul
git fetch origin claude/ai-design-automation-app-xju0x > nul 2>&1
git reset --hard FETCH_HEAD > nul 2>&1
goto loop
