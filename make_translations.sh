#!/bin/bash

# Detect virtual environment and activate it
if [ -d "venv" ]; then
    source venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
else
    echo "No virtual environment found! Make sure 'venv' or '.venv' exists."
    exit 1
fi

# Run makemessages while ignoring both venv and .venv directories
python manage.py makemessages -l uk -l zh_HK -i venv -i .venv

# Deactivate virtual environment
deactivate

echo "Translation files updated!"
