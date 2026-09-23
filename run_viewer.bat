@echo off
rem 启动静电势表面查看器 (无控制台)
cd /d "%~dp0"
start "" pythonw esp_viewer_gui.py
