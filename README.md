# AgentKVM

A PC automation agent that uses screenshots, cliclick, and osascript to control macOS via LLM instructions.

## Features

- **Multi-backend LLM support**: SSH-GPU (Ollama/qwen), Codex CLI, Claude CLI
- **Visual understanding**: Takes screenshots and sends to vision-capable LLMs
- **macOS control**: Uses `cliclick` for mouse/keyboard and `osascript` for AppleScript
- **Action history**: JSON-structured history provides context across iterations
- **Safety validation**: Commands are validated before execution

## Requirements

- macOS (uses `screencapture`, `cliclick`, `osascript`)
- `cliclick` - install via `brew install cliclick`
- Python 3.6+
- One of the following LLM backends:
  - **ssh-gpu**: `aiocr` tool + SSH access to GPU server with Ollama
  - **codex**: OpenAI Codex CLI (`codex -p ...`)
  - **claude**: Claude Code CLI

## Installation

```bash
git clone git@github.com:gsportelli/agentkvm.git
cd agentkvm
chmod +x agent.py
```

## Usage

```bash
# Using Claude CLI
./agent.py -b claude "Open Safari and search for weather"

# Using SSH-GPU backend
./agent.py -b ssh-gpu --ssh-host gpu.example.com "Open Gmail in Brave and read first email"

# Using Codex
./agent.py -b codex "Click the Settings icon and enable dark mode"

# With verbose output
./agent.py -b claude -v "Open Terminal"

# Reset history and start fresh
./agent.py -b claude -r "Open Finder"
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
| `-h, --help` | Show help |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `SSH_GPU_HOST` | Default SSH host for ssh-gpu backend |

## How It Works

1. **Screenshot**: Captures the current screen using `screencapture`
2. **Analyze**: Sends screenshot + prompt + history to LLM
3. **Parse**: Extracts observation, reasoning, and command from response
4. **Validate**: Checks command safety (must be `cliclick` or `osascript`)
5. **Execute**: Runs the command to interact with the system
6. **Record**: Saves action to history for context in next iteration
7. **Loop**: Repeats until goal achieved or max iterations

## Action History

The agent maintains structured JSON history (`action_history.json`) that tracks:

```json
{
  "goal": "Open Safari and search for weather",
  "started_at": "2024-01-15T10:30:00",
  "status": "in_progress",
  "iterations": 3,
  "actions": [
    {
      "iteration": 1,
      "timestamp": "2024-01-15T10:30:05",
      "observation": "Desktop with dock visible at bottom",
      "reasoning": "Need to click Safari icon in the dock",
      "command": "cliclick c:512,950",
      "result": "OK"
    }
  ]
}
```

This history is included in each LLM prompt, giving the model context about past actions.

## Available Commands

### cliclick (mouse/keyboard)

```bash
cliclick m:x,y        # Move mouse to coordinates
cliclick c:x,y        # Click at position
cliclick c:.          # Click at current position
cliclick dc:x,y       # Double-click
cliclick rc:x,y       # Right-click
cliclick t:"text"     # Type text
cliclick kp:enter     # Press key (enter, tab, space, delete, escape)
cliclick kp:cmd-l     # Key combo (cmd-l, cmd-t, cmd-w, cmd-a, cmd-c, cmd-v)
cliclick m:100,200 c: # Combined: move and click
```

### osascript (AppleScript)

```bash
osascript -e 'tell application "Safari" to activate'
osascript -e 'open location "https://example.com"'
osascript -e 'tell application "System Events" to click menu item "X" of menu "Y" of menu bar 1 of process "AppName"'
```

## Safety

Commands are validated before execution:
- Must start with `cliclick` or `osascript`
- Shell metacharacters (`; & | > < $ \``) are rejected
- Dangerous osascript patterns (`rm`, `sudo`, `curl`, `wget`) are blocked

## Files

| File | Description |
|------|-------------|
| `agent.py` | Main agent script (Python) |
| `agent.sh` | Legacy bash version |
| `action_history.json` | Structured action history (JSON) |
| `action_history.txt` | Human-readable action history |
| `currentscreen.png` | Latest screenshot |
| `currentscreen.md` | Latest LLM response |
| `logs/` | Execution logs |
| `past_screens/` | Archived screenshots and responses |

## License

MIT
