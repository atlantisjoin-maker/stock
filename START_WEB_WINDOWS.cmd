@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%CD%\src"
set "ASTOCK_HOME=%CD%"
set PYTHON=
if exist ".venv\Scripts\python.exe" set "PYTHON=.venv\Scripts\python.exe"
if not defined PYTHON where py >nul 2>nul && set "PYTHON=py"
if not defined PYTHON where python >nul 2>nul && set "PYTHON=python"
if not defined PYTHON (
  echo Python 3.10 or newer was not found.
  pause
  exit /b 1
)
%PYTHON% -m astock_terminal
if errorlevel 1 pause
