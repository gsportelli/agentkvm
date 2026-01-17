#!/usr/bin/env python3
"""
AgentKVM - PC automation agent using screenshots and LLM
Supports multiple backends: ssh-gpu (aiocr), codex, claude
Cross-platform: macOS and Linux (Debian/Ubuntu)
"""

import argparse
import base64
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List, Dict

SCRIPT_DIR = Path(__file__).parent.resolve()
ACTION_HISTORY_FILE = SCRIPT_DIR / "action_history.json"
ACTION_HISTORY_TXT = SCRIPT_DIR / "action_history.txt"
SCREENSHOT = SCRIPT_DIR / "currentscreen.png"
MD_OUT = SCRIPT_DIR / "currentscreen.md"
PAST_DIR = SCRIPT_DIR / "past_screens"
LOG_DIR = SCRIPT_DIR / "logs"

# Global verbose flag
VERBOSE = False

# Platform detection
class Platform:
    MACOS = "macos"
    LINUX = "linux"
    UNKNOWN = "unknown"


def detect_platform() -> str:
    """Detect the current operating system."""
    system = platform.system().lower()
    if system == "darwin":
        return Platform.MACOS
    elif system == "linux":
        return Platform.LINUX
    return Platform.UNKNOWN


def detect_display_server() -> str:
    """Detect Linux display server (X11 or Wayland)."""
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    elif os.environ.get("DISPLAY"):
        return "x11"
    return "unknown"


# Dependency definitions per platform
DEPENDENCIES: Dict[str, Dict[str, dict]] = {
    Platform.MACOS: {
        "cliclick": {
            "check": "cliclick",
            "install": "brew install cliclick",
            "description": "Mouse/keyboard automation tool",
            "required": True,
        },
        "screencapture": {
            "check": "screencapture",
            "install": "Built-in on macOS",
            "description": "Screenshot utility",
            "required": True,
        },
    },
    Platform.LINUX: {
        "xdotool": {
            "check": "xdotool",
            "install": "sudo apt install xdotool",
            "description": "Mouse/keyboard automation tool (X11)",
            "required": True,
        },
        "scrot": {
            "check": "scrot",
            "install": "sudo apt install scrot",
            "description": "Screenshot utility",
            "required": False,  # One of scrot/gnome-screenshot/import is needed
        },
        "gnome-screenshot": {
            "check": "gnome-screenshot",
            "install": "sudo apt install gnome-screenshot",
            "description": "GNOME screenshot utility",
            "required": False,
        },
        "import": {
            "check": "import",
            "install": "sudo apt install imagemagick",
            "description": "ImageMagick screenshot utility",
            "required": False,
        },
        "xclip": {
            "check": "xclip",
            "install": "sudo apt install xclip",
            "description": "Clipboard utility",
            "required": False,
        },
        "wmctrl": {
            "check": "wmctrl",
            "install": "sudo apt install wmctrl",
            "description": "Window management utility",
            "required": False,
        },
    },
}

# Backend-specific dependencies
BACKEND_DEPENDENCIES = {
    "ssh-gpu": {
        "aiocr": {
            "check": "aiocr",
            "install": "Install from https://github.com/gsportelli/aiocr or add to PATH",
            "description": "AI OCR tool for SSH-GPU backend",
            "required": True,
        },
    },
    "codex": {
        "codex": {
            "check": "codex",
            "install": "npm install -g @openai/codex",
            "description": "OpenAI Codex CLI",
            "required": True,
        },
    },
    "claude": {
        "claude": {
            "check": "claude",
            "install": "npm install -g @anthropic-ai/claude-code",
            "description": "Claude Code CLI",
            "required": True,
        },
    },
}


def check_command_exists(cmd: str) -> bool:
    """Check if a command exists in PATH."""
    return shutil.which(cmd) is not None


def check_dependencies(plat: str, backend: str) -> Tuple[List[str], List[str]]:
    """
    Check for required dependencies.
    Returns (missing_required, missing_optional) lists with install instructions.
    """
    missing_required = []
    missing_optional = []

    # Platform dependencies
    if plat in DEPENDENCIES:
        for name, info in DEPENDENCIES[plat].items():
            if not check_command_exists(info["check"]):
                entry = f"  - {name}: {info['description']}\n    Install: {info['install']}"
                if info["required"]:
                    missing_required.append(entry)
                else:
                    missing_optional.append(entry)

    # For Linux, check if at least one screenshot tool is available
    if plat == Platform.LINUX:
        screenshot_tools = ["scrot", "gnome-screenshot", "import"]
        has_screenshot = any(check_command_exists(t) for t in screenshot_tools)
        if not has_screenshot:
            missing_required.append(
                "  - Screenshot tool (install ONE of the following):\n"
                "    - scrot: sudo apt install scrot (recommended)\n"
                "    - gnome-screenshot: sudo apt install gnome-screenshot\n"
                "    - import: sudo apt install imagemagick"
            )
            # Remove individual entries from optional
            missing_optional = [m for m in missing_optional
                              if not any(t in m for t in screenshot_tools)]

    # Backend dependencies
    if backend in BACKEND_DEPENDENCIES:
        for name, info in BACKEND_DEPENDENCIES[backend].items():
            if not check_command_exists(info["check"]):
                entry = f"  - {name}: {info['description']}\n    Install: {info['install']}"
                if info["required"]:
                    missing_required.append(entry)

    return missing_required, missing_optional


def print_dependency_status(plat: str, backend: str) -> bool:
    """
    Print dependency status and return True if all required deps are present.
    """
    missing_required, missing_optional = check_dependencies(plat, backend)

    if missing_required:
        print("=" * 60)
        print("MISSING REQUIRED DEPENDENCIES")
        print("=" * 60)
        print("\nThe following required tools are missing:\n")
        for dep in missing_required:
            print(dep)
            print()
        print("Please install the missing dependencies and run again.")
        print("=" * 60)
        return False

    if missing_optional:
        print("-" * 60)
        print("Note: Some optional tools are missing (not required):\n")
        for dep in missing_optional:
            print(dep)
            print()
        print("-" * 60)
        print()

    return True


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def log(msg: str, verbose_only: bool = False):
    """Print timestamped log message."""
    if verbose_only and not VERBOSE:
        return
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def ensure_dirs():
    """Create necessary directories."""
    PAST_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)


def rotate_file(path: Path, prefix: str):
    """Move file to past_screens with timestamp."""
    if path.exists():
        dest = PAST_DIR / f"{prefix}_{timestamp()}{path.suffix}"
        shutil.move(str(path), str(dest))


def get_screen_resolution(plat: str) -> Tuple[int, int]:
    """Get screen resolution for the current platform."""
    try:
        if plat == Platform.MACOS:
            result = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True, text=True
            )
            match = re.search(r'Resolution: (\d+) x (\d+)', result.stdout)
            if match:
                return int(match.group(1)), int(match.group(2))
        elif plat == Platform.LINUX:
            result = subprocess.run(
                ["xdpyinfo"], capture_output=True, text=True
            )
            match = re.search(r'dimensions:\s+(\d+)x(\d+)', result.stdout)
            if match:
                return int(match.group(1)), int(match.group(2))
    except Exception:
        pass
    return 1920, 1080  # Default fallback


def take_screenshot(plat: str) -> Path:
    """Capture screen using platform-appropriate tool."""
    if plat == Platform.MACOS:
        subprocess.run(["screencapture", str(SCREENSHOT)], check=True)
    elif plat == Platform.LINUX:
        # Try screenshot tools in order of preference
        if check_command_exists("scrot"):
            subprocess.run(["scrot", str(SCREENSHOT)], check=True)
        elif check_command_exists("gnome-screenshot"):
            subprocess.run(["gnome-screenshot", "-f", str(SCREENSHOT)], check=True)
        elif check_command_exists("import"):
            subprocess.run(["import", "-window", "root", str(SCREENSHOT)], check=True)
        else:
            raise RuntimeError("No screenshot tool available")
    else:
        raise RuntimeError(f"Unsupported platform: {plat}")

    return SCREENSHOT


def image_to_base64(path: Path) -> str:
    """Convert image to base64 string."""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


class ActionHistory:
    """Manages structured action history for context carryover."""

    def __init__(self, goal: str, reset: bool = False):
        self.path = ACTION_HISTORY_FILE
        self.txt_path = ACTION_HISTORY_TXT

        if reset or not self.path.exists():
            self._init_history(goal)
        else:
            self._load()
            if self.data["goal"] != goal:
                self.data["goal"] = goal
                self._save()

    def _init_history(self, goal: str):
        self.data = {
            "goal": goal,
            "started_at": datetime.now().isoformat(),
            "status": "in_progress",
            "iterations": 0,
            "actions": []
        }
        self._save()

        with open(self.txt_path, "w") as f:
            f.write(f"# Action History\n")
            f.write(f"Goal: {goal}\n")
            f.write(f"Started: {datetime.now()}\n")
            f.write(f"Status: In Progress\n\n")
            f.write("## Actions\n")

    def _load(self):
        with open(self.path) as f:
            self.data = json.load(f)

    def _save(self):
        with open(self.path, "w") as f:
            json.dump(self.data, f, indent=2)

    def add_action(self, iteration: int, observation: str, reasoning: str,
                   command: str, result: str):
        self.data["iterations"] = iteration
        self.data["actions"].append({
            "iteration": iteration,
            "timestamp": datetime.now().isoformat(),
            "observation": observation,
            "reasoning": reasoning,
            "command": command,
            "result": result
        })
        self._save()

        with open(self.txt_path, "a") as f:
            f.write(f"\n### Iteration {iteration} ({datetime.now().strftime('%H:%M:%S')})\n")
            f.write(f"**Observation:** {observation}\n")
            f.write(f"**Reasoning:** {reasoning}\n")
            f.write(f"**Command:** `{command}`\n")
            f.write(f"**Result:** {result}\n")

    def mark_completed(self):
        self.data["status"] = "completed"
        self.data["completed_at"] = datetime.now().isoformat()
        self._save()

        with open(self.txt_path, "a") as f:
            f.write(f"\n## Goal Achieved at {datetime.now()}\n")

    def get_context(self, max_actions: int = 10) -> str:
        lines = [
            f"Goal: {self.data['goal']}",
            f"Status: {self.data['status']}",
            f"Total iterations so far: {self.data['iterations']}",
            ""
        ]

        actions = self.data["actions"][-max_actions:]
        if actions:
            lines.append(f"Recent actions (last {len(actions)}):")
            for a in actions:
                lines.append(f"  [{a['iteration']}] {a['command']}")
                if a["result"]:
                    lines.append(f"      Result: {a['result'][:100]}...")
                lines.append(f"      Reasoning: {a['reasoning'][:150]}...")
        else:
            lines.append("No actions taken yet.")

        return "\n".join(lines)


def build_prompt_macos(goal: str, history_context: str, width: int, height: int) -> str:
    """Build prompt for macOS."""
    return f"""# Role
You are an autonomous screen agent controlling a Mac. You interact using **cliclick** for mouse/keyboard and **osascript** for AppleScript automation.

# Current Goal
{goal}

# Screen Information
- Resolution: {width} x {height} pixels
- Coordinate system: (0,0) is top-left

# Available Commands

## cliclick (mouse/keyboard control)
- Move mouse: cliclick m:x,y
- Click at position: cliclick c:x,y
- Click current: cliclick c:.
- Double-click: cliclick dc:x,y
- Right-click: cliclick rc:x,y
- Type text: cliclick t:"text here"
- Press key: cliclick kp:enter (also: return, tab, space, delete, escape, arrow-up/down/left/right)
- Key combo: cliclick kp:cmd-l, kp:cmd-t, kp:cmd-w, kp:cmd-a, kp:cmd-c, kp:cmd-v
- Combined: cliclick m:100,200 c:.

## osascript (AppleScript - for complex automation)
- Get frontmost app: osascript -e 'tell application "System Events" to get name of first process whose frontmost is true'
- Click menu item: osascript -e 'tell application "System Events" to click menu item "X" of menu "Y" of menu bar 1 of process "AppName"'
- Open URL: osascript -e 'open location "https://example.com"'
- Activate app: osascript -e 'tell application "AppName" to activate'

# Context and Past Actions
{history_context}

# OUTPUT FORMAT (MUST FOLLOW EXACTLY)
Respond with exactly these three blocks:

###OBS
<What you observe in the screenshot - 1-2 sentences>

###THINK
<Your reasoning about what to do next - 2-3 sentences>

###CMD
<ONE command line only - must start with 'cliclick' or 'osascript'>

Rules:
- ###CMD must be exactly ONE line starting with 'cliclick' or 'osascript'
- No semicolons, pipes, redirects, or command chaining
- If goal is achieved, write: cliclick kp:escape
  And include "GOAL ACHIEVED" at the start of ###OBS"""


def build_prompt_linux(goal: str, history_context: str, width: int, height: int) -> str:
    """Build prompt for Linux."""
    return f"""# Role
You are an autonomous screen agent controlling a Linux desktop. You interact using **xdotool** for mouse/keyboard control.

# Current Goal
{goal}

# Screen Information
- Resolution: {width} x {height} pixels
- Coordinate system: (0,0) is top-left

# Available Commands

## xdotool (mouse/keyboard control)
- Move mouse: xdotool mousemove x y
- Click at position: xdotool mousemove x y click 1
- Left click: xdotool click 1
- Right click: xdotool click 3
- Middle click: xdotool click 2
- Double-click: xdotool click --repeat 2 --delay 100 1
- Type text: xdotool type "text here"
- Type with delay: xdotool type --delay 50 "text here"
- Press key: xdotool key Return (also: Tab, space, BackSpace, Escape, Up, Down, Left, Right)
- Key combo: xdotool key ctrl+l, ctrl+t, ctrl+w, ctrl+a, ctrl+c, ctrl+v, alt+F4
- Key with super: xdotool key super (Windows/Super key)
- Focus window by name: xdotool search --name "Window Title" windowactivate
- Get active window: xdotool getactivewindow

## wmctrl (window management - optional)
- List windows: wmctrl -l
- Activate window: wmctrl -a "Window Title"
- Close window: wmctrl -c "Window Title"

## xclip (clipboard - optional)
- Copy to clipboard: echo "text" | xclip -selection clipboard
- Paste from clipboard: xclip -selection clipboard -o

# Context and Past Actions
{history_context}

# OUTPUT FORMAT (MUST FOLLOW EXACTLY)
Respond with exactly these three blocks:

###OBS
<What you observe in the screenshot - 1-2 sentences>

###THINK
<Your reasoning about what to do next - 2-3 sentences>

###CMD
<ONE command line only - must start with 'xdotool', 'wmctrl', or 'xclip'>

Rules:
- ###CMD must be exactly ONE line starting with 'xdotool', 'wmctrl', or 'xclip'
- No semicolons, pipes, redirects, or command chaining (except for xclip with echo)
- If goal is achieved, write: xdotool key Escape
  And include "GOAL ACHIEVED" at the start of ###OBS"""


def build_prompt(goal: str, history_context: str, plat: str, width: int, height: int) -> str:
    """Build the prompt for the LLM based on platform."""
    if plat == Platform.MACOS:
        return build_prompt_macos(goal, history_context, width, height)
    elif plat == Platform.LINUX:
        return build_prompt_linux(goal, history_context, width, height)
    else:
        raise RuntimeError(f"Unsupported platform: {plat}")


def extract_blocks(text: str) -> Tuple[str, str, str]:
    """Extract OBS, THINK, CMD blocks from model output."""
    obs = think = cmd = ""

    match = re.search(r'###OBS\s*\n(.*?)(?=###|$)', text, re.DOTALL)
    if match:
        obs = match.group(1).strip()

    match = re.search(r'###THINK\s*\n(.*?)(?=###|$)', text, re.DOTALL)
    if match:
        think = match.group(1).strip()

    match = re.search(r'###CMD\s*\n(.*?)(?=###|$)', text, re.DOTALL)
    if match:
        lines = [l.strip() for l in match.group(1).strip().split('\n') if l.strip()]
        if lines:
            cmd = lines[0]

    return obs, think, cmd


def validate_command(cmd: str, plat: str) -> Tuple[bool, str]:
    """Validate command for safety. Returns (is_valid, error_message)."""
    if not cmd:
        return False, "Empty command"

    # Platform-specific allowed commands
    if plat == Platform.MACOS:
        allowed_prefixes = ("cliclick", "osascript")
    elif plat == Platform.LINUX:
        allowed_prefixes = ("xdotool", "wmctrl", "xclip")
    else:
        return False, f"Unsupported platform: {plat}"

    if not any(cmd.startswith(p) for p in allowed_prefixes):
        return False, f"Command must start with one of {allowed_prefixes}. Got: {cmd}"

    # Common dangerous patterns to reject
    dangerous = ["rm ", "sudo", "curl ", "wget ", "kill ", "pkill", ">>",
                 "&&", "||", ";", "`", "$("]

    # Allow pipe only for xclip
    if cmd.startswith("xclip") or "| xclip" in cmd:
        # Allow echo ... | xclip pattern
        if not re.match(r'^echo\s+"[^"]*"\s*\|\s*xclip', cmd) and not cmd.startswith("xclip"):
            pass  # Will be caught by dangerous check
    elif "|" in cmd:
        dangerous.append("|")

    for d in dangerous:
        if d in cmd:
            return False, f"Command contains forbidden pattern '{d}'"

    return True, ""


class LLMBackend:
    """Base class for LLM backends."""

    def call(self, prompt: str, screenshot_path: Path) -> str:
        raise NotImplementedError


class SSHGPUBackend(LLMBackend):
    """Backend using aiocr via SSH tunnel to GPU server."""

    def __init__(self, host: str, port: int = 25114):
        self.host = host
        self.port = port

    def call(self, prompt: str, screenshot_path: Path) -> str:
        log(f"Calling aiocr via SSH to {self.host}...", verbose_only=True)

        log_file = LOG_DIR / f"aiocr_{timestamp()}.log"

        result = subprocess.run(
            ["aiocr", str(screenshot_path), "-p", prompt, "-j", "1",
             "-H", self.host, "--port", str(self.port)],
            capture_output=True,
            text=True
        )

        with open(log_file, "w") as f:
            f.write(f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}")

        if result.returncode != 0:
            raise RuntimeError(f"aiocr failed. See: {log_file}")

        if MD_OUT.exists():
            return MD_OUT.read_text()

        return result.stdout


class CodexBackend(LLMBackend):
    """Backend using Codex CLI."""

    def call(self, prompt: str, screenshot_path: Path) -> str:
        log("Calling Codex CLI...", verbose_only=True)

        full_prompt = f"{prompt}\n\nAnalyze the attached screenshot and respond with ###OBS, ###THINK, and ###CMD blocks."

        result = subprocess.run(
            ["codex", "-p", full_prompt, "--image", str(screenshot_path)],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            raise RuntimeError(f"codex failed: {result.stderr}")

        return result.stdout


class ClaudeBackend(LLMBackend):
    """Backend using Claude CLI (claude-code)."""

    def call(self, prompt: str, screenshot_path: Path) -> str:
        log("Calling Claude CLI...", verbose_only=True)

        full_prompt = f"""{prompt}

Analyze the attached screenshot and respond with ###OBS, ###THINK, and ###CMD blocks.
IMPORTANT: Output ONLY the three blocks (###OBS, ###THINK, ###CMD) with no other text."""

        result = subprocess.run(
            ["claude", "-p", full_prompt, "--allowedTools", "", str(screenshot_path)],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            raise RuntimeError(f"claude failed: {result.stderr}")

        return result.stdout


def execute_command(cmd: str) -> Tuple[bool, str]:
    """Execute a validated command. Returns (success, output)."""
    log_file = LOG_DIR / f"cmd_{timestamp()}.out"

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30
        )

        output = result.stdout + result.stderr
        with open(log_file, "w") as f:
            f.write(output)

        if result.returncode == 0:
            return True, output[:200] if output else "OK"
        else:
            return False, f"FAILED: {output[:200]}"

    except subprocess.TimeoutExpired:
        return False, "TIMEOUT after 30s"
    except Exception as e:
        return False, f"ERROR: {str(e)}"


def get_backend(name: str, ssh_host: str = None, ssh_port: int = 25114) -> LLMBackend:
    """Factory function to create LLM backend."""
    if name == "ssh-gpu":
        if not ssh_host:
            ssh_host = os.environ.get("SSH_GPU_HOST")
        if not ssh_host:
            raise ValueError("ssh-gpu backend requires SSH_GPU_HOST env or --ssh-host flag")
        return SSHGPUBackend(ssh_host, ssh_port)
    elif name == "codex":
        return CodexBackend()
    elif name == "claude":
        return ClaudeBackend()
    else:
        raise ValueError(f"Unknown backend: {name}")


def main():
    global VERBOSE

    parser = argparse.ArgumentParser(
        description="PC automation agent using screenshots and LLM (cross-platform)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s -b claude "Open Firefox and search for weather"
  %(prog)s -b ssh-gpu --ssh-host gpu.local "Open Gmail in browser"
  %(prog)s -b codex "Click the Settings icon"
  %(prog)s --check-deps  # Check dependencies without running

Backends:
  ssh-gpu   Use aiocr via SSH tunnel to GPU server with Ollama
  codex     Use OpenAI Codex CLI
  claude    Use Claude CLI

Platforms:
  macOS     Uses cliclick, osascript, screencapture
  Linux     Uses xdotool, scrot/gnome-screenshot/import
"""
    )
    parser.add_argument("goal", nargs="?", help="The goal for the agent to achieve")
    parser.add_argument("-b", "--backend", default="ssh-gpu",
                        choices=["ssh-gpu", "codex", "claude"],
                        help="LLM backend (default: ssh-gpu)")
    parser.add_argument("--ssh-host", help="SSH host for ssh-gpu backend")
    parser.add_argument("--ssh-port", type=int, default=25114,
                        help="SSH port for Ollama (default: 25114)")
    parser.add_argument("-m", "--max-iter", type=int, default=50,
                        help="Maximum iterations (default: 50)")
    parser.add_argument("-r", "--reset", action="store_true",
                        help="Reset action history before starting")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Verbose output")
    parser.add_argument("--check-deps", action="store_true",
                        help="Check dependencies and exit")

    args = parser.parse_args()
    VERBOSE = args.verbose

    # Detect platform
    plat = detect_platform()
    if plat == Platform.UNKNOWN:
        print(f"Error: Unsupported platform: {platform.system()}", file=sys.stderr)
        print("Supported platforms: macOS, Linux", file=sys.stderr)
        sys.exit(1)

    log(f"Detected platform: {plat}")
    if plat == Platform.LINUX:
        display = detect_display_server()
        log(f"Display server: {display}")
        if display == "wayland":
            print("Warning: Wayland detected. xdotool may have limited functionality.")
            print("Consider using X11 or installing ydotool for better Wayland support.")

    # Check dependencies
    if not print_dependency_status(plat, args.backend):
        sys.exit(1)

    if args.check_deps:
        print("All required dependencies are installed.")
        sys.exit(0)

    # Goal is required for actual run
    if not args.goal:
        parser.error("the following arguments are required: goal")

    ensure_dirs()

    # Initialize backend
    try:
        backend = get_backend(args.backend, args.ssh_host, args.ssh_port)
    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Get screen resolution
    width, height = get_screen_resolution(plat)
    log(f"Screen resolution: {width}x{height}")

    # Initialize history
    history = ActionHistory(args.goal, reset=args.reset)

    log(f"Starting agent with backend: {args.backend}")
    log(f"Goal: {args.goal}")
    log(f"Max iterations: {args.max_iter}")
    print()

    for iteration in range(1, args.max_iter + 1):
        log(f"=== Iteration {iteration} ===")

        # Rotate old files
        rotate_file(SCREENSHOT, "screen")
        rotate_file(MD_OUT, "model")

        # Take screenshot
        log("Taking screenshot...", verbose_only=True)
        take_screenshot(plat)

        # Build prompt with history context
        prompt = build_prompt(args.goal, history.get_context(), plat, width, height)

        # Call LLM
        log(f"Analyzing screen with {args.backend}...")
        try:
            response = backend.call(prompt, SCREENSHOT)
        except Exception as e:
            log(f"ERROR: LLM call failed: {e}")
            sys.exit(1)

        # Save response
        MD_OUT.write_text(response)

        # Extract blocks
        observation, reasoning, command = extract_blocks(response)

        log(f"Observation: {observation[:100]}...")
        log(f"Reasoning: {reasoning[:100]}...")
        log(f"Command: {command}")

        # Check for goal achieved
        if "GOAL ACHIEVED" in observation.upper():
            log("GOAL ACHIEVED!")
            history.add_action(iteration, observation, reasoning, command, "Goal completed")
            history.mark_completed()
            break

        # Validate command
        is_valid, error = validate_command(command, plat)
        if not is_valid:
            log(f"ERROR: Invalid command - {error}")
            shutil.move(str(MD_OUT), str(PAST_DIR / f"model_invalid_{timestamp()}.md"))
            history.add_action(iteration, observation, reasoning, command, f"REJECTED: {error}")
            sys.exit(1)

        # Execute command
        log(f"Executing: {command}")
        success, result = execute_command(command)
        if not success:
            log(f"WARNING: Command failed: {result}")

        # Update history
        history.add_action(iteration, observation, reasoning, command, result)

        # Small delay for UI updates
        import time
        time.sleep(1.5)

    else:
        log(f"WARNING: Reached maximum iterations ({args.max_iter}) without achieving goal")

    log(f"Agent finished. History saved to: {ACTION_HISTORY_FILE}")


if __name__ == "__main__":
    main()
