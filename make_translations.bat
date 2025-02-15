@echo off
REM Detect virtual environment folder and activate it
if exist venv\Scripts\activate (
    call venv\Scripts\activate
) else if exist .venv\Scripts\activate (
    call .venv\Scripts\activate
) else (
    echo No virtual environment found! Make sure venv or .venv exists.
    exit /b 1
)

REM Run makemessages ignoring both venv and .venv directories
python manage.py makemessages -l uk -l zh_HK -i venv -i .venv

REM Deactivate virtual environment
deactivate

echo Translation files updated!
pause
