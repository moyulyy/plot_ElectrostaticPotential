@echo off
rem ============================================================
rem  打包便携程序包 (PyInstaller onedir)
rem  产物: dist\ESP_Viewer\  (exe + _internal\)
rem  整个文件夹拷到任意 Windows 电脑双击即可运行, 无需 Python。
rem
rem  设 ESP_ONEFILE=1 可改为单文件 exe (不推荐, 启动慢)。
rem ============================================================
setlocal EnableExtensions
cd /d "%~dp0"

set "PY=D:\miniconda3\envs\chem_env\python.exe"
if not exist "%PY%" (
  for %%I in (python.exe) do set "PY=%%~$PATH:I"
)
if not exist "%PY%" (
  echo [ERROR] 未找到 Python 解释器。
  echo         期望: D:\miniconda3\envs\chem_env\python.exe
  pause
  exit /b 1
)

echo ============================================================
echo  使用解释器: %PY%
if "%ESP_ONEFILE%"=="1" (
  echo  模式: ONEFILE ^(单文件 exe^)
) else (
  echo  模式: PORTABLE FOLDER ^(便携文件夹, 推荐^)
)
echo ============================================================

rem 让本环境的 DLL 目录优先, 避免混入 base conda 的 DLL
for %%D in ("%PY%") do set "ENVDIR=%%~dpD"
if exist "%ENVDIR%Library\bin" set "PATH=%ENVDIR%Library\bin;%ENVDIR%DLLs;%ENVDIR%Scripts;%PATH%"

"%PY%" -c "import PyInstaller" 2>nul
if errorlevel 1 (
  echo [INFO] 正在安装 PyInstaller ...
  "%PY%" -m pip install pyinstaller || (echo [ERROR] pip install 失败 & pause & exit /b 1)
)

rem 生成内置样例 (若缺失)
if not exist "samples\LOCPOT" (
  if exist "test\LOCPOT" (
    echo [INFO] 生成内置样例 samples\ ...
    "%PY%" make_samples.py test 3
  )
)

"%PY%" -m PyInstaller --noconfirm --clean ESP_Viewer.spec
if errorlevel 1 (
  echo.
  echo [ERROR] 打包失败, 请查看上面的信息。
  pause
  exit /b 1
)

rem 便携说明放到 exe 旁边
if exist "PORTABLE_README.txt" (
  if exist "dist\ESP_Viewer" copy /y "PORTABLE_README.txt" "dist\ESP_Viewer\使用说明.txt" >nul
)

echo.
echo ============================================================
if "%ESP_ONEFILE%"=="1" (
  echo  完成!  单文件: %~dp0dist\ESP_Viewer.exe
) else (
  echo  完成!  便携文件夹: %~dp0dist\ESP_Viewer\
  echo         运行其中的 ESP_Viewer.exe 即可。
)
echo ============================================================
pause
