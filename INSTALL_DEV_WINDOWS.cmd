@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul || (echo Python launcher not found.& pause & exit /b 1)
if not exist .venv py -3.10 -m venv .venv
set "P=.venv\Scripts\python.exe"
%P% -m pip install --upgrade pip setuptools wheel -i https://mirrors.aliyun.com/pypi/simple/ --timeout 1200 --retries 20
%P% -m pip install -e ".[market]" -i https://mirrors.aliyun.com/pypi/simple/ --timeout 1200 --retries 20 --prefer-binary
%P% -c "import astock_terminal; print('Installed', astock_terminal.__version__)"
pause
