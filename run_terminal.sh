cat << 'EOF' > run_terminal.sh
#!/bin/bash

# Resolve the absolute path to this project directory
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# AppleScript command to launch a dedicated macOS Terminal window
osascript <<APPLESCRIPT
tell application "Terminal"
    activate
    do script "cd \"$DIR\" && source .venv/bin/activate && caffeinate -dimsu python monitor.py"
end tell
APPLESCRIPT
EOF