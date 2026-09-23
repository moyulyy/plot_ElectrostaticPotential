@echo off
rem 启动静电势表面查看器 (保留控制台, 便于查看报错)
cd /d "%~dp0"
python esp_viewer_gui.py
pause
