#!/usr/bin/env bash
set -e
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.main &
API_PID=$!
cd web
npm install
npm run dev &
UI_PID=$!
trap 'kill $API_PID $UI_PID' EXIT
wait
