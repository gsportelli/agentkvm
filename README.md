# AgentKVM

A cross-platform PC automation agent that uses screenshots and LLM to control your computer via natural language instructions.

## Features

- **Cross-platform**: Works on macOS and Linux (X11 and Wayland)
- **Multi-backend LLM support**: Ollama (local), Codex CLI, Claude CLI
- **Visual understanding**: Takes screenshots and sends to vision-capable LLMs
- **Platform-aware control**:
  - macOS: `cliclick` + `osascript`
  - Linux X11: `xdotool` + `wmctrl`
  - Linux Wayland: `ydotool` + `grim`
- **Multi-command execution**: 1-5 commands per LLM call for efficiency
- **Action history**: JSON-structured history provides context across iterations
- **Interactive model selection**: Prompts for model if not specified
- **Safety validation**: Commands are validated before execution

## Requirements

### macOS

```bash
# Required
brew install cliclick
```

### Linux (X11)

```bash
# Input tool
sudo apt install xdotool

# Screenshot tool (one of)
sudo apt install scrot              # Recommended
sudo apt install gnome-screenshot

# Optional
sudo apt install wmctrl             # Window management
sudo apt install xclip              # Clipboard
```

### Linux (Wayland)

```bash
# Input tool (requires ydotoold service)
sudo apt install ydotool
sudo systemctl enable --now ydotool

# Screenshot tool
sudo apt install grim               # Recommended for Wayland
sudo apt install gnome-screenshot   # Alternative

# Optional
sudo apt install wl-clipboard       # Clipboard (wl-copy/wl-paste)
```

### Ollama (default backend)

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull a vision model
ollama pull llava
# or
ollama pull moondream
ollama pull minicpm-v
```

### Alternative backends

```bash
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
./agent.py --check-deps
```

## Usage

```bash
# Using Ollama (will prompt for model selection)
./agent.py "Open Firefox and search for weather"

# Specify model directly
./agent.py --model llava "Open browser and go to google.com"

# Connect to remote Ollama
./agent.py --host 192.168.1.100 --port 11434 --model llava "Open terminal"

# Using Claude CLI
./agent.py -b claude "Open Safari and search for weather"

# Using Codex
./agent.py -b codex "Click the Settings icon"

# With verbose output
./agent.py -v --model llava "Open file manager"

# Reset history and start fresh
./agent.py -r --model moondream "Open browser"

# Check dependencies only
./agent.py --check-deps
```

## Options

| Option | Description |
|--------|-------------|
| `-b, --backend` | LLM backend: `ollama`, `codex`, `claude` (default: ollama) |
| `--host` | Ollama host (default: localhost, or OLLAMA_HOST env) |
| `--port` | Ollama port (default: 11434, or OLLAMA_PORT env) |
| `--model` | Ollama model to use (will prompt if not specified) |
| `-m, --max-iter` | Maximum iterations (default: 50) |
| `-r, --reset` | Reset action history before starting |
| `-v, --verbose` | Verbose output |
| `--check-deps` | Check dependencies and exit |
| `-h, --help` | Show help |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `OLLAMA_HOST` | Default Ollama host |
| `OLLAMA_PORT` | Default Ollama port |

## How It Works

1. **Detect Platform**: Identifies macOS or Linux (X11/Wayland), checks for required tools
2. **Select Model**: If no model specified, shows available vision models to choose from
3. **Screenshot**: Captures screen using platform-appropriate tool
4. **Analyze**: Sends screenshot + prompt + history to LLM
5. **Parse**: Extracts observation, reasoning, and command(s) from response
6. **Validate**: Checks all commands for safety before execution
7. **Execute**: Runs command sequence with delays (stops on first failure)
8. **Record**: Saves action to history for context in next iteration
9. **Loop**: Repeats until goal achieved or max iterations

## Multi-Command Support

The agent can execute **1-5 commands per LLM call** to reduce API calls:

```
###CMD
ydotool mousemove -a 500 300
ydotool click 0xC0
ydotool type "hello world"
ydotool key enter
```

## Available Commands

### macOS

#### cliclick
```bash
cliclick m:x,y        # Move mouse
cliclick c:x,y        # Click at position
cliclick dc:x,y       # Double-click
cliclick rc:x,y       # Right-click
cliclick t:"text"     # Type text
cliclick kp:enter     # Press key
cliclick kp:cmd-l     # Key combo
```

### Linux (xdotool - X11)

```bash
xdotool mousemove x y           # Move mouse
xdotool mousemove x y click 1   # Move and click
xdotool click 1                 # Left click (1=left, 3=right)
xdotool type "text here"        # Type text
xdotool key Return              # Press key
xdotool key ctrl+l              # Key combo
xdotool key super               # Super key
```

### Linux (ydotool - Wayland)

```bash
ydotool mousemove -a x y        # Move mouse (absolute)
ydotool click 0xC0              # Left click
ydotool click 0xC1              # Right click
ydotool type "text here"        # Type text
ydotool key enter               # Press key
ydotool key ctrl+l              # Key combo
ydotool key super               # Super key
```

## Platform Notes

### Wayland

- Uses `ydotool` instead of `xdotool` (works natively on Wayland)
- Uses `grim` for screenshots
- Requires `ydotoold` service running: `sudo systemctl enable --now ydotool`

### Model Selection

If `--model` is not specified, the agent will:
1. Connect to Ollama
2. List available vision models (filtered by keywords: vision, llava, moondream, etc.)
3. Prompt you to select one

```
Available vision models:
  1. llava:latest
  2. moondream:latest
  3. minicpm-v:latest

Select model (1-3):
```

## Safety

Commands are validated before execution:

**Allowed commands:**
- macOS: `cliclick`, `osascript`
- Linux (X11): `xdotool`, `wmctrl`
- Linux (Wayland): `ydotool`, `wmctrl`

**Blocked patterns:**
- Shell metacharacters: `; && || >> \` $()`
- Dangerous commands: `rm`, `sudo`, `curl`, `wget`, `kill`

## Files

| File | Description |
|------|-------------|
| `agent.py` | Main agent script (Python) |
| `action_history.json` | Structured action history (JSON) |
| `action_history.txt` | Human-readable action history |
| `currentscreen.png` | Latest screenshot |
| `currentscreen.md` | Latest LLM response |
| `logs/` | Execution logs |
| `past_screens/` | Archived screenshots and responses |

## Troubleshooting

### Cannot connect to Ollama

```bash
# Make sure Ollama is running
ollama serve

# Check if it's accessible
curl http://localhost:11434/api/tags
```

### ydotool not working

```bash
# Check if ydotoold is running
systemctl status ydotool

# Enable and start service
sudo systemctl enable --now ydotool

# You may need to add user to input group
sudo usermod -aG input $USER
# Then logout and login
```

### No screenshot on Wayland

```bash
# Install grim for Wayland screenshots
sudo apt install grim
```

### cliclick permission denied (macOS)

System Preferences > Security & Privacy > Privacy > Accessibility
Add Terminal (or your terminal app) to the list.

## License

MIT
