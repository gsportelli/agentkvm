# AgentKVM

A cross-platform PC automation agent that uses screenshots and LLM to control your computer via natural language instructions.

## Features

- **Cross-platform**: Works on macOS and Linux (Debian/Ubuntu)
- **Multi-backend LLM support**: SSH-GPU (Ollama/qwen), Codex CLI, Claude CLI
- **Visual understanding**: Takes screenshots and sends to vision-capable LLMs
- **Platform-aware control**:
  - macOS: `cliclick` + `osascript`
  - Linux: `xdotool` + `wmctrl` + `xclip`
- **Action history**: JSON-structured history provides context across iterations
- **Dependency checking**: Automatically checks for required tools on first run
- **Safety validation**: Commands are validated before execution

## Requirements

### macOS

```bash
# Required
brew install cliclick

# Built-in (no install needed)
# - screencapture
# - osascript
```

### Linux (Debian/Ubuntu)

```bash
# Required
sudo apt install xdotool

# Screenshot tool (install ONE)
sudo apt install scrot              # Recommended
# OR
sudo apt install gnome-screenshot   # For GNOME
# OR
sudo apt install imagemagick        # Provides 'import' command

# Optional (for advanced features)
sudo apt install wmctrl             # Window management
sudo apt install xclip              # Clipboard operations
```

### LLM Backends

Install one of the following based on your chosen backend:

```bash
# For ssh-gpu backend
# Install aiocr from https://github.com/gsportelli/aiocr

# For codex backend
npm install -g @openai/codex

# For claude backend
npm install -g @anthropic-ai/claude-code
```

## Installation

```bash
git clone git@github.com:gsportelli/agentkvm.git
cd agentkvm
chmod +x agent.py

# Check dependencies
./agent.py --check-deps -b claude
```

## Usage

```bash
# Using Claude CLI
./agent.py -b claude "Open Firefox and search for weather"

# Using SSH-GPU backend
./agent.py -b ssh-gpu --ssh-host gpu.example.com "Open Gmail in browser"

# Using Codex
./agent.py -b codex "Click the Settings icon and enable dark mode"

# With verbose output
./agent.py -b claude -v "Open Terminal"

# Reset history and start fresh
./agent.py -b claude -r "Open file manager"

# Check dependencies only
./agent.py --check-deps -b ssh-gpu
```

## Options

| Option | Description |
|--------|-------------|
| `-b, --backend` | LLM backend: `ssh-gpu`, `codex`, `claude` (default: ssh-gpu) |
| `--ssh-host` | SSH host for ssh-gpu backend |
| `--ssh-port` | SSH port for Ollama (default: 25114) |
| `-m, --max-iter` | Maximum iterations (default: 50) |
| `-r, --reset` | Reset action history before starting |
| `-v, --verbose` | Verbose output |
| `--check-deps` | Check dependencies and exit |
| `-h, --help` | Show help |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `SSH_GPU_HOST` | Default SSH host for ssh-gpu backend |

## How It Works

1. **Detect Platform**: Identifies macOS or Linux, checks for required tools
2. **Screenshot**: Captures screen using platform-appropriate tool
3. **Analyze**: Sends screenshot + prompt + history to LLM
4. **Parse**: Extracts observation, reasoning, and command from response
5. **Validate**: Checks command safety (platform-specific allowed commands)
6. **Execute**: Runs the command to interact with the system
7. **Record**: Saves action to history for context in next iteration
8. **Loop**: Repeats until goal achieved or max iterations

## Action History

The agent maintains structured JSON history (`action_history.json`) that tracks:

```json
{
  "goal": "Open Firefox and search for weather",
  "started_at": "2024-01-15T10:30:00",
  "status": "in_progress",
  "iterations": 3,
  "actions": [
    {
      "iteration": 1,
      "timestamp": "2024-01-15T10:30:05",
      "observation": "Desktop with dock visible at bottom",
      "reasoning": "Need to click browser icon in the dock",
      "command": "cliclick c:512,950",
      "result": "OK"
    }
  ]
}
```

This history is included in each LLM prompt, giving the model memory of past actions.

## Available Commands

### macOS

#### cliclick (mouse/keyboard)
```bash
cliclick m:x,y        # Move mouse to coordinates
cliclick c:x,y        # Click at position
cliclick c:.          # Click at current position
cliclick dc:x,y       # Double-click
cliclick rc:x,y       # Right-click
cliclick t:"text"     # Type text
cliclick kp:enter     # Press key (enter, tab, space, delete, escape)
cliclick kp:cmd-l     # Key combo (cmd-l, cmd-t, cmd-w, cmd-a, cmd-c, cmd-v)
```

#### osascript (AppleScript)
```bash
osascript -e 'tell application "Safari" to activate'
osascript -e 'open location "https://example.com"'
```

### Linux

#### xdotool (mouse/keyboard)
```bash
xdotool mousemove x y           # Move mouse
xdotool mousemove x y click 1   # Move and click
xdotool click 1                 # Left click (1=left, 2=middle, 3=right)
xdotool type "text here"        # Type text
xdotool key Return              # Press key (Return, Tab, Escape, etc.)
xdotool key ctrl+l              # Key combo (ctrl+l, ctrl+t, alt+F4, etc.)
xdotool key super               # Super/Windows key
```

#### wmctrl (window management)
```bash
wmctrl -l                       # List windows
wmctrl -a "Window Title"        # Activate window
wmctrl -c "Window Title"        # Close window
```

#### xclip (clipboard)
```bash
echo "text" | xclip -selection clipboard  # Copy to clipboard
xclip -selection clipboard -o             # Paste from clipboard
```

## Platform Notes

### Wayland (Linux)

If running on Wayland, xdotool has limited functionality. The agent will warn you about this. For better Wayland support, consider:
- Running under XWayland
- Using `ydotool` instead (requires separate setup)

### Screen Resolution

The agent automatically detects screen resolution:
- macOS: via `system_profiler`
- Linux: via `xdpyinfo`

Falls back to 1920x1080 if detection fails.

## Safety

Commands are validated before execution:

**Allowed commands:**
- macOS: `cliclick`, `osascript`
- Linux: `xdotool`, `wmctrl`, `xclip`

**Blocked patterns:**
- Shell metacharacters: `; && || > >> | \` $()`
- Dangerous commands: `rm`, `sudo`, `curl`, `wget`, `kill`

## Files

| File | Description |
|------|-------------|
| `agent.py` | Main agent script (Python) |
| `agent.sh` | Legacy bash version (macOS only) |
| `action_history.json` | Structured action history (JSON) |
| `action_history.txt` | Human-readable action history |
| `currentscreen.png` | Latest screenshot |
| `currentscreen.md` | Latest LLM response |
| `logs/` | Execution logs |
| `past_screens/` | Archived screenshots and responses |

## Troubleshooting

### Missing dependencies

Run `./agent.py --check-deps -b <backend>` to see what's missing and get install instructions.

### xdotool not working on Wayland

```bash
# Check display server
echo $XDG_SESSION_TYPE

# If "wayland", try running app under XWayland or switch to X11 session
```

### Permission denied for screenshot (Linux)

Some desktop environments require additional permissions. Try:
```bash
# For GNOME
gsettings set org.gnome.desktop.privacy disable-microphone true
```

### cliclick not clicking correctly (macOS)

Ensure accessibility permissions are granted:
System Preferences > Security & Privacy > Privacy > Accessibility

## License

MIT
