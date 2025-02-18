@echo off
setlocal

REM Start ngrok in a separate window and keep it running
start "" cmd /k "ngrok.exe http 8000"

REM Prompt user for new ngrok URL
set /p new_item="Enter a new ngrok URL (e.g., xxxx.ngrok-free.app): "

REM Remove "https://" prefix if present
set new_item=%new_item:https://=%

REM Write Python code to a file that only modifies ALLOWED_HOSTS
echo import os > temp_script.py
echo import re >> temp_script.py
echo. >> temp_script.py
echo file_path = os.path.join(os.path.dirname(__file__), 'pavilion', 'settings.py') >> temp_script.py
echo. >> temp_script.py
echo with open(file_path, 'r') as f: >> temp_script.py
echo     lines = f.readlines() >> temp_script.py
echo. >> temp_script.py
echo ngrok_pattern = re.compile(r'\b\w+-\w+-\w+-\w+-\w+-\w+-\w+-\w+.ngrok-free\.app\b') >> temp_script.py
echo in_allowed_hosts = False >> temp_script.py
echo allowed_hosts_lines = [] >> temp_script.py
echo updated_lines = [] >> temp_script.py
echo found_ngrok = False >> temp_script.py
echo. >> temp_script.py
echo for line in lines: >> temp_script.py
echo     if line.strip().startswith("ALLOWED_HOSTS ="): >> temp_script.py
echo         in_allowed_hosts = True >> temp_script.py
echo     if in_allowed_hosts: >> temp_script.py
echo         allowed_hosts_lines.append(line.strip()) >> temp_script.py
echo         if line.strip().endswith("]"): >> temp_script.py
echo             in_allowed_hosts = False >> temp_script.py
echo             exec("\n".join(allowed_hosts_lines)) >> temp_script.py
echo             for i, host in enumerate(ALLOWED_HOSTS): >> temp_script.py
echo                 if ngrok_pattern.search(host): >> temp_script.py
echo                     ALLOWED_HOSTS[i] = "%new_item%" >> temp_script.py
echo                     found_ngrok = True >> temp_script.py
echo             if not found_ngrok: >> temp_script.py
echo                 ALLOWED_HOSTS.append("%new_item%") >> temp_script.py
echo             updated_lines.append(f"ALLOWED_HOSTS = [\n") >> temp_script.py
echo             for host in ALLOWED_HOSTS: >> temp_script.py
echo                 updated_lines.append(f"    '{host}',\n") >> temp_script.py
echo             updated_lines.append("]\n") >> temp_script.py
echo     else: >> temp_script.py
echo         updated_lines.append(line) >> temp_script.py
echo. >> temp_script.py
echo with open(file_path, 'w') as f: >> temp_script.py
echo     f.writelines(updated_lines) >> temp_script.py

REM Execute the Python script
python temp_script.py

REM Clean up
del temp_script.py

REM Activate virtual environment
call "venv\Scripts\activate"

REM Run Django development server in the same terminal
python manage.py collectstatic --noinput
python manage.py compilemessages
python manage.py runserver

endlocal