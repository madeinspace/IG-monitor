#!/bin/bash
cd "$(dirname "$0")" || exit 1

# Execute the local venv interpreter with caffeinate
caffeinate -dimsu ./.venv/bin/python ./monitor.py

# Keep window open if the script ever exits/crashes so you can see why
echo ""
read -p "Process ended. Press [ENTER] to close window..."
