@echo off
rem Windows 启动脚本：等价于 Unix 下的 bin/pp-ocr。
rem 装好 uv/pip 的 pp-ocr 命令后不需要它；这里用于直接从源码目录运行。
setlocal
set "PROJECT_DIR=%~dp0.."
set "PYTHONPATH=%PROJECT_DIR%;%PYTHONPATH%"

if exist "%PROJECT_DIR%\.venv\Scripts\python.exe" (
  "%PROJECT_DIR%\.venv\Scripts\python.exe" -m pipeline.cli %*
) else (
  python -m pipeline.cli %*
)
exit /b %ERRORLEVEL%
