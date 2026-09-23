@echo off
rem 安装运行 / 打包所需依赖
setlocal EnableExtensions
cd /d "%~dp0"

set "PY=D:\miniconda3\envs\chem_env\python.exe"
if not exist "%PY%" (
  for %%I in (python.exe) do set "PY=%%~$PATH:I"
)
if not exist "%PY%" (
  echo [ERROR] 未找到 Python 解释器。
  pause
  exit /b 1
)

echo 使用解释器: %PY%
"%PY%" -m pip install -r requirements.txt
pause
