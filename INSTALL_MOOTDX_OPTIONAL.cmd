@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul || (echo Python launcher not found.& pause & exit /b 1)
if not exist .venv py -3.10 -m venv .venv
set "P=.venv\Scripts\python.exe"
%P% -m pip install -r requirements-market.txt -i https://mirrors.aliyun.com/pypi/simple/ --timeout 1200 --retries 20 --prefer-binary
%P% -c "import mootdx,httpx,httpcore; print('Optional market provider installed')"
pause
