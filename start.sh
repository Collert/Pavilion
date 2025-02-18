#!/bin/bash

# Start ngrok in a new terminal window and keep it running
nohup ngrok http 8000 &

# Prompt user for new ngrok URL
read -p "Enter a new ngrok URL (e.g., xxxx.ngrok-free.app): " new_item

# Remove "https://" prefix if present
new_item=${new_item#https://}

# Write Python script to modify ALLOWED_HOSTS
cat <<EOL > temp_script.py
import os
import re

file_path = os.path.join(os.path.dirname(__file__), 'pavilion', 'settings.py')

with open(file_path, 'r') as f:
    lines = f.readlines()

ngrok_pattern = re.compile(r'\b\w+-\w+-\w+-\w+-\w+-\w+-\w+-\w+.ngrok-free\.app\b')
in_allowed_hosts = False
allowed_hosts_lines = []
updated_lines = []
found_ngrok = False

for line in lines:
    if line.strip().startswith("ALLOWED_HOSTS ="):
        in_allowed_hosts = True
    if in_allowed_hosts:
        allowed_hosts_lines.append(line.strip())
        if line.strip().endswith("]"):
            in_allowed_hosts = False
            exec("\n".join(allowed_hosts_lines))
            for i, host in enumerate(ALLOWED_HOSTS):
                if ngrok_pattern.search(host):
                    ALLOWED_HOSTS[i] = "$new_item"
                    found_ngrok = True
            if not found_ngrok:
                ALLOWED_HOSTS.append("$new_item")
            updated_lines.append("ALLOWED_HOSTS = [\n")
            for host in ALLOWED_HOSTS:
                updated_lines.append(f"    '{host}',\n")
            updated_lines.append("]\n")
    else:
        updated_lines.append(line)

with open(file_path, 'w') as f:
    f.writelines(updated_lines)
EOL

# Execute the Python script
python3 temp_script.py

# Clean up
rm temp_script.py

# Activate virtual environment
source venv/bin/activate

# Run Django development server
python manage.py collectstatic --noinput
python manage.py compilemessages
python manage.py runserver
