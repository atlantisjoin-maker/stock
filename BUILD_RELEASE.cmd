@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%CD%\src"
if exist ".venv\Scripts\python.exe" (set "P=.venv\Scripts\python.exe") else (set "P=py")
%P% scripts\package_release.py
if errorlevel 1 pause
