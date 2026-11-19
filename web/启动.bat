@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0.."
rem 数据库放在同步盘之外（避免同步客户端锁住 SQLite 的 -shm）
if exist "D:\astock_data\astock.db" set ASTOCK_DB=D:\astock_data\astock.db
rem 如果 Docker 容器已经在跑，就别再起本机实例（会抢 127.0.0.1:5001）
set "RUNNING="
for /f "tokens=*" %%s in ('docker inspect -f "{{.State.Running}}" astock-screener 2^>nul') do set "RUNNING=%%s"
if /i "%RUNNING%"=="true" goto running
rem 端口被占用时会自动顺延（5001 -> 5002 ...），并自动打开浏览器
python web\app.py --port 5001
pause
exit /b 0
:running
echo.
echo  [提示] Docker 容器 astock-screener 已经在运行，端口 5001 由它占用。
echo         直接访问 http://localhost:5001 即可；
echo         要用本机版请先执行：docker compose down
echo.
pause
exit /b 1
