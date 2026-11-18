@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0.."
rem 数据库放在同步盘之外（避免同步客户端锁住 SQLite 的 -shm）
if exist "D:\astock_data\astock.db" set ASTOCK_DB=D:\astock_data\astock.db
rem 端口被占用时会自动顺延（5001 -> 5002 ...），并自动打开浏览器
python web\app.py --port 5001
pause
