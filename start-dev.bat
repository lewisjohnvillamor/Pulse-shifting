@echo off
if not exist .venv (
  py -m venv .venv
)
call .venv\Scripts\activate
pip install -r requirements.txt

start "PulseShift API" cmd /k "call .venv\Scripts\activate && python -m app.main"
cd web
if not exist node_modules (
  call npm install
)
start "PulseShift UI" cmd /k "npm run dev"
timeout /t 3 >nul
start http://127.0.0.1:5173
