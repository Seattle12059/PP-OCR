@echo off
rem Windows 启动脚本：等价于 Unix 下的 bin/ocr。
setlocal
set "PROJECT_DIR=%~dp0.."
set "PYTHONPATH=%PROJECT_DIR%;%PYTHONPATH%"

if exist "%PROJECT_DIR%\.venv\Scripts\python.exe" (
  "%PROJECT_DIR%\.venv\Scripts\python.exe" "%PROJECT_DIR%\main.py" %*
) else (
  python "%PROJECT_DIR%\main.py" %*
)
exit /b %ERRORLEVEL%
