#!/bin/bash
cd "$(dirname "$0")"

# Activate the venv and execute monitor with caffeinate
source .venv/bin/activate
caffeinate -dimsu python monitor.py

# Keep window open if the script ever exits/crashes so you can see why
echo ""
read -p "Process ended. Press [ENTER] to close window..."
