@echo off
cd /d "%~dp0"
echo RFID 監聽啟動中...
python rfid_listener.py
echo.
echo 程式已結束，按任意鍵關閉視窗
pause
