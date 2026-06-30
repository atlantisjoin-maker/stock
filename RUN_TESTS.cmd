@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%CD%\src"
if exist ".venv\Scripts\python.exe" (.venv\Scripts\python.exe -m unittest discover -s tests -v) else (py -m unittest discover -s tests -v)
pause
