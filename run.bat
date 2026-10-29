@echo off
rem 开发期启动脚本：直接用系统 Python 跑源码，不做打包。
cd /d "%~dp0"
python -X utf8 main.py %*

