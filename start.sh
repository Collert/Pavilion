#!/bin/bash
# Local dev server, optionally reachable through ngrok.
# The ngrok hostname is passed to Django via EXTRA_HOSTS (settings.py no longer gets rewritten).
# Careful: this exposes your dev server (DEBUG on) to the internet while it runs.

nohup ngrok http 8000 > /dev/null 2>&1 &
read -p "Paste the ngrok URL (e.g. xxxx.ngrok-free.app), or press Enter to skip: " EXTRA_HOSTS
export EXTRA_HOSTS

source venv/bin/activate
python manage.py collectstatic --noinput
python manage.py compilemessages
python manage.py runserver
