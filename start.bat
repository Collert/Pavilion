@echo off
setlocal
REM Local dev server, optionally reachable through ngrok.
REM The ngrok hostname is passed to Django via EXTRA_HOSTS (settings.py no longer gets rewritten).
REM Careful: this exposes your dev server (DEBUG on) to the internet while it runs.

start "" cmd /k "ngrok.exe http 8000"
set /p EXTRA_HOSTS="Paste the ngrok URL (e.g. xxxx.ngrok-free.app), or press Enter to skip: "

call "venv\Scripts\activate"
python manage.py collectstatic --noinput
python manage.py compilemessages
python manage.py runserver
endlocal
