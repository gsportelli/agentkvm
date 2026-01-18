#!/usr/bin/env python3
"""
AgentKVM - PC automation agent using screenshots and LLM
Supports multiple backends: ollama, codex, claude
Cross-platform: macOS and Linux (X11 and Wayland)
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
import time
import urllib.request
import urllib.error
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


class DisplayServer:
    X11 = "x11"
    WAYLAND = "wayland"
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
        return DisplayServer.WAYLAND
    elif os.environ.get("DISPLAY"):
        return DisplayServer.X11
    return DisplayServer.UNKNOWN


def detect_input_tool() -> str:
    """Detect available input tool (ydotool for Wayland, xdotool for X11)."""
    if check_command_exists("ydotool"):
        return "ydotool"
    elif check_command_exists("xdotool"):
        return "xdotool"
    return None


def check_command_exists(cmd: str) -> bool:
    """Check if a command exists in PATH."""
    return shutil.which(cmd) is not None


def get_linux_dependencies(display_server: str) -> Dict[str, dict]:
    """Get Linux dependencies based on display server."""
    deps = {}

    # Input tool - prefer ydotool on Wayland, xdotool on X11
    if display_server == DisplayServer.WAYLAND:
        deps["ydotool"] = {
            "check": "ydotool",
            "install": "sudo apt install ydotool && sudo systemctl enable --now ydotool",
            "description": "Mouse/keyboard automation tool (Wayland)",
            "required": False,  # One of ydotool/xdotool needed
        }
        deps["xdotool"] = {
            "check": "xdotool",
            "install": "sudo apt install xdotool (limited on Wayland)",
            "description": "Mouse/keyboard automation tool (X11)",
            "required": False,
        }
    else:
        deps["xdotool"] = {
            "check": "xdotool",
            "install": "sudo apt install xdotool",
            "description": "Mouse/keyboard automation tool (X11)",
            "required": False,
        }
        deps["ydotool"] = {
            "check": "ydotool",
            "install": "sudo apt install ydotool",
            "description": "Mouse/keyboard automation tool (Wayland)",
            "required": False,
        }

    # Screenshot tools
    if display_server == DisplayServer.WAYLAND:
        deps["grim"] = {
            "check": "grim",
            "install": "sudo apt install grim",
            "description": "Screenshot utility (Wayland)",
            "required": False,
        }
    deps["scrot"] = {
        "check": "scrot",
        "install": "sudo apt install scrot",
        "description": "Screenshot utility (X11)",
        "required": False,
    }
    deps["gnome-screenshot"] = {
        "check": "gnome-screenshot",
        "install": "sudo apt install gnome-screenshot",
        "description": "GNOME screenshot utility",
        "required": False,
    }

    # Optional tools
    deps["wl-copy"] = {
        "check": "wl-copy",
        "install": "sudo apt install wl-clipboard",
        "description": "Clipboard utility (Wayland)",
        "required": False,
    }
    deps["xclip"] = {
        "check": "xclip",
        "install": "sudo apt install xclip",
        "description": "Clipboard utility (X11)",
        "required": False,
    }

    return deps


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
}

# Backend-specific dependencies (no longer need aiocr)
BACKEND_DEPENDENCIES = {
    "ollama": {},  # No external deps, just needs Ollama running
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


def check_dependencies(plat: str, backend: str, display_server: str = None) -> Tuple[List[str], List[str]]:
    """Check for required dependencies."""
    missing_required = []
    missing_optional = []

    # Platform dependencies
    if plat == Platform.MACOS:
        for name, info in DEPENDENCIES[Platform.MACOS].items():
            if not check_command_exists(info["check"]):
                entry = f"  - {name}: {info['description']}\n    Install: {info['install']}"
                if info["required"]:
                    missing_required.append(entry)
                else:
                    missing_optional.append(entry)
    elif plat == Platform.LINUX:
        linux_deps = get_linux_dependencies(display_server or DisplayServer.X11)
        for name, info in linux_deps.items():
            if not check_command_exists(info["check"]):
                entry = f"  - {name}: {info['description']}\n    Install: {info['install']}"
                missing_optional.append(entry)

        # Check for at least one input tool
        if not check_command_exists("ydotool") and not check_command_exists("xdotool"):
            missing_required.append(
                "  - Input tool (install ONE of the following):\n"
                "    - ydotool: sudo apt install ydotool (recommended for Wayland)\n"
                "    - xdotool: sudo apt install xdotool (for X11)"
            )

        # Check for at least one screenshot tool
        screenshot_tools = ["grim", "scrot", "gnome-screenshot", "import"]
        if not any(check_command_exists(t) for t in screenshot_tools):
            if display_server == DisplayServer.WAYLAND:
                missing_required.append(
                    "  - Screenshot tool (install ONE of the following):\n"
                    "    - grim: sudo apt install grim (recommended for Wayland)\n"
                    "    - gnome-screenshot: sudo apt install gnome-screenshot"
                )
            else:
                missing_required.append(
                    "  - Screenshot tool (install ONE of the following):\n"
                    "    - scrot: sudo apt install scrot (recommended)\n"
                    "    - gnome-screenshot: sudo apt install gnome-screenshot\n"
                    "    - import: sudo apt install imagemagick"
                )

    # Backend dependencies
    if backend in BACKEND_DEPENDENCIES:
        for name, info in BACKEND_DEPENDENCIES[backend].items():
            if not check_command_exists(info["check"]):
                entry = f"  - {name}: {info['description']}\n    Install: {info['install']}"
                if info.get("required", False):
                    missing_required.append(entry)

    return missing_required, missing_optional


def print_dependency_status(plat: str, backend: str, display_server: str = None) -> bool:
    """Print dependency status and return True if all required deps are present."""
    missing_required, missing_optional = check_dependencies(plat, backend, display_server)

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

    if missing_optional and VERBOSE:
        print("-" * 60)
        print("Note: Some optional tools are missing:\n")
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


def get_screen_resolution(plat: str, display_server: str = None) -> Tuple[int, int]:
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
            if display_server == DisplayServer.WAYLAND:
                # Try wlr-randr or swaymsg for Wayland
                result = subprocess.run(
                    ["wlr-randr"], capture_output=True, text=True
                )
                match = re.search(r'(\d+)x(\d+)', result.stdout)
                if match:
                    return int(match.group(1)), int(match.group(2))
            # Fallback to xdpyinfo
            result = subprocess.run(
                ["xdpyinfo"], capture_output=True, text=True
            )
            match = re.search(r'dimensions:\s+(\d+)x(\d+)', result.stdout)
            if match:
                return int(match.group(1)), int(match.group(2))
    except Exception:
        pass
    return 1920, 1080  # Default fallback


def take_screenshot(plat: str, display_server: str = None) -> Path:
    """Capture screen using platform-appropriate tool."""
    if plat == Platform.MACOS:
        subprocess.run(["screencapture", str(SCREENSHOT)], check=True)
    elif plat == Platform.LINUX:
        # Try screenshot tools in order of preference based on display server
        if display_server == DisplayServer.WAYLAND:
            if check_command_exists("grim"):
                subprocess.run(["grim", str(SCREENSHOT)], check=True)
            elif check_command_exists("gnome-screenshot"):
                subprocess.run(["gnome-screenshot", "-f", str(SCREENSHOT)], check=True)
            else:
                raise RuntimeError("No Wayland screenshot tool available (install grim)")
        else:
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
                   commands: List[str], results: List[Tuple[str, bool, str]]):
        """Add an action (possibly multi-command) to history."""
        self.data["iterations"] = iteration

        results_summary = []
        for cmd, success, res in results:
            status = "OK" if success else "FAIL"
            results_summary.append(f"[{status}] {res[:50]}")
        results_str = " | ".join(results_summary) if results_summary else "No execution"

        self.data["actions"].append({
            "iteration": iteration,
            "timestamp": datetime.now().isoformat(),
            "observation": observation,
            "reasoning": reasoning,
            "commands": commands,
            "commands_count": len(commands),
            "executed_count": len(results),
            "all_succeeded": all(r[1] for r in results) if results else False,
            "result_summary": results_str
        })
        self._save()

        with open(self.txt_path, "a") as f:
            f.write(f"\n### Iteration {iteration} ({datetime.now().strftime('%H:%M:%S')})\n")
            f.write(f"**Observation:** {observation}\n")
            f.write(f"**Reasoning:** {reasoning}\n")
            f.write(f"**Commands ({len(commands)}):**\n")
            for i, cmd in enumerate(commands):
                executed = i < len(results)
                if executed:
                    success = results[i][1]
                    status = "OK" if success else "FAIL"
                    f.write(f"  {i+1}. `{cmd}` [{status}]\n")
                else:
                    f.write(f"  {i+1}. `{cmd}` [NOT EXECUTED]\n")
            f.write(f"**Result:** {results_str}\n")

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
                if "commands" in a:
                    cmd_count = a.get("commands_count", len(a["commands"]))
                    exec_count = a.get("executed_count", cmd_count)
                    cmds = a["commands"]
                    if cmd_count == 1:
                        lines.append(f"  [{a['iteration']}] {cmds[0]}")
                    else:
                        lines.append(f"  [{a['iteration']}] {cmd_count} commands ({exec_count} executed):")
                        for cmd in cmds[:3]:
                            lines.append(f"      - {cmd[:60]}...")
                    result = a.get("result_summary", "")
                else:
                    lines.append(f"  [{a['iteration']}] {a.get('command', 'N/A')}")
                    result = a.get("result", "")

                if result:
                    lines.append(f"      Result: {result[:100]}...")
                reasoning = a.get("reasoning", "")
                if reasoning:
                    lines.append(f"      Reasoning: {reasoning[:150]}...")
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
- Open URL: osascript -e 'open location "https://example.com"'
- Activate app: osascript -e 'tell application "AppName" to activate'

# Context and Past Actions
{history_context}

# OUTPUT FORMAT (MUST FOLLOW EXACTLY)
Respond with exactly these three blocks:

###OBS
<What you observe in the screenshot - 1-2 sentences>

###THINK
<Your reasoning about what to do next. Plan a sequence of actions if you're confident about multiple steps.>

###CMD
<One command per line, 1-5 commands total. Each must start with 'cliclick' or 'osascript'>

Rules:
- ###CMD can have 1 to 5 commands, ONE PER LINE
- Each command must start with 'cliclick' or 'osascript'
- Use multiple commands when you're CONFIDENT about a sequence (e.g., click then type)
- Use single command when outcome is uncertain or needs visual verification
- No semicolons, pipes, redirects, or command chaining within a line
- Commands execute sequentially with a small delay between them
- If goal is achieved, write: cliclick kp:escape
  And include "GOAL ACHIEVED" at the start of ###OBS"""


def build_prompt_linux(goal: str, history_context: str, width: int, height: int, input_tool: str) -> str:
    """Build prompt for Linux with appropriate input tool."""

    if input_tool == "ydotool":
        input_commands = """## ydotool (mouse/keyboard control for Wayland)
- Move mouse: ydotool mousemove -a x y
- Click at position: ydotool mousemove -a x y && ydotool click 0xC0
- Left click: ydotool click 0xC0
- Right click: ydotool click 0xC1
- Type text: ydotool type "text here"
- Type with delay: ydotool type --delay 50 "text here"
- Press key: ydotool key enter (also: tab, space, backspace, esc, up, down, left, right)
- Key combo: ydotool key ctrl+l, ctrl+t, ctrl+w, ctrl+a, ctrl+c, ctrl+v, alt+F4
- Super key: ydotool key super

Note: ydotool uses absolute coordinates with -a flag"""
        allowed_tools = "'ydotool'"
        escape_cmd = "ydotool key esc"
        example_cmds = """###CMD
ydotool mousemove -a 500 300
ydotool click 0xC0
ydotool type "hello world"
ydotool key enter"""
    else:  # xdotool
        input_commands = """## xdotool (mouse/keyboard control for X11)
- Move mouse: xdotool mousemove x y
- Click at position: xdotool mousemove x y click 1
- Left click: xdotool click 1
- Right click: xdotool click 3
- Double-click: xdotool click --repeat 2 --delay 100 1
- Type text: xdotool type "text here"
- Type with delay: xdotool type --delay 50 "text here"
- Press key: xdotool key Return (also: Tab, space, BackSpace, Escape, Up, Down, Left, Right)
- Key combo: xdotool key ctrl+l, ctrl+t, ctrl+w, ctrl+a, ctrl+c, ctrl+v, alt+F4
- Super key: xdotool key super
- Focus window: xdotool search --name "Window Title" windowactivate"""
        allowed_tools = "'xdotool', 'wmctrl'"
        escape_cmd = "xdotool key Escape"
        example_cmds = """###CMD
xdotool mousemove 500 300 click 1
xdotool type "hello world"
xdotool key Return"""

    return f"""# Role
You are an autonomous screen agent controlling a Linux desktop. You interact using **{input_tool}** for mouse/keyboard control.

# Current Goal
{goal}

# Screen Information
- Resolution: {width} x {height} pixels
- Coordinate system: (0,0) is top-left

# Available Commands

{input_commands}

## wmctrl (window management - optional)
- Activate window: wmctrl -a "Window Title"
- Close window: wmctrl -c "Window Title"

# Context and Past Actions
{history_context}

# OUTPUT FORMAT (MUST FOLLOW EXACTLY)
Respond with exactly these three blocks:

###OBS
<What you observe in the screenshot - 1-2 sentences>

###THINK
<Your reasoning about what to do next. Plan a sequence of actions if you're confident about multiple steps.>

###CMD
<One command per line, 1-5 commands total. Each must start with {allowed_tools}>

Rules:
- ###CMD can have 1 to 5 commands, ONE PER LINE
- Each command must start with {allowed_tools}
- Use multiple commands when you're CONFIDENT about a sequence (e.g., click then type)
- Use single command when outcome is uncertain or needs visual verification
- No semicolons, pipes, redirects, or command chaining within a line
- Commands execute sequentially with a small delay between them
- If goal is achieved, write: {escape_cmd}
  And include "GOAL ACHIEVED" at the start of ###OBS

Examples of multi-command sequences:
{example_cmds}"""


def build_prompt(goal: str, history_context: str, plat: str, width: int, height: int, input_tool: str = None) -> str:
    """Build the prompt for the LLM based on platform."""
    if plat == Platform.MACOS:
        return build_prompt_macos(goal, history_context, width, height)
    elif plat == Platform.LINUX:
        return build_prompt_linux(goal, history_context, width, height, input_tool or "xdotool")
    else:
        raise RuntimeError(f"Unsupported platform: {plat}")


def extract_blocks(text: str) -> Tuple[str, str, List[str]]:
    """Extract OBS, THINK, CMD blocks from model output."""
    obs = think = ""
    commands: List[str] = []

    match = re.search(r'###OBS\s*\n(.*?)(?=###|$)', text, re.DOTALL)
    if match:
        obs = match.group(1).strip()

    match = re.search(r'###THINK\s*\n(.*?)(?=###|$)', text, re.DOTALL)
    if match:
        think = match.group(1).strip()

    match = re.search(r'###CMD\s*\n(.*?)(?=###|$)', text, re.DOTALL)
    if match:
        lines = [l.strip() for l in match.group(1).strip().split('\n') if l.strip()]
        commands = lines[:5]

    return obs, think, commands


def validate_command(cmd: str, plat: str, input_tool: str = None) -> Tuple[bool, str]:
    """Validate a single command for safety."""
    if not cmd:
        return False, "Empty command"

    # Platform-specific allowed commands
    if plat == Platform.MACOS:
        allowed_prefixes = ("cliclick", "osascript")
    elif plat == Platform.LINUX:
        if input_tool == "ydotool":
            allowed_prefixes = ("ydotool", "wmctrl")
        else:
            allowed_prefixes = ("xdotool", "wmctrl")
    else:
        return False, f"Unsupported platform: {plat}"

    if not any(cmd.startswith(p) for p in allowed_prefixes):
        return False, f"Command must start with one of {allowed_prefixes}. Got: {cmd}"

    # Dangerous patterns to reject
    dangerous = ["rm ", "sudo", "curl ", "wget ", "kill ", "pkill", ">>",
                 ";", "`", "$(", "eval "]

    # Allow && only for ydotool mousemove + click pattern
    if input_tool == "ydotool" and "&&" in cmd:
        if not re.match(r'^ydotool mousemove.*&&\s*ydotool click', cmd):
            dangerous.append("&&")
    elif "&&" in cmd:
        dangerous.append("&&")

    if "||" in cmd:
        dangerous.append("||")

    for d in dangerous:
        if d in cmd:
            return False, f"Command contains forbidden pattern '{d}'"

    return True, ""


def validate_commands(commands: List[str], plat: str, input_tool: str = None) -> Tuple[bool, str, int]:
    """Validate a list of commands."""
    if not commands:
        return False, "No commands provided", -1

    for i, cmd in enumerate(commands):
        is_valid, error = validate_command(cmd, plat, input_tool)
        if not is_valid:
            return False, f"Command {i+1}: {error}", i

    return True, "", -1


class LLMBackend:
    """Base class for LLM backends."""

    def call(self, prompt: str, screenshot_path: Path) -> str:
        raise NotImplementedError


class OllamaBackend(LLMBackend):
    """Backend using Ollama API directly."""

    def __init__(self, host: str = "localhost", port: int = 11434, model: str = None):
        self.host = host
        self.port = port
        self.model = model
        self.base_url = f"http://{host}:{port}"

    def list_models(self) -> List[str]:
        """List available vision models from Ollama."""
        try:
            url = f"{self.base_url}/api/tags"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                models = [m["name"] for m in data.get("models", [])]
                # Filter for likely vision models
                vision_keywords = ["vision", "llava", "bakllava", "moondream", "minicpm-v", "qwen"]
                vision_models = [m for m in models if any(k in m.lower() for k in vision_keywords)]
                return vision_models if vision_models else models
        except Exception as e:
            log(f"Failed to list models: {e}", verbose_only=True)
            return []

    def check_connection(self) -> bool:
        """Check if Ollama is reachable."""
        try:
            url = f"{self.base_url}/api/tags"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def call(self, prompt: str, screenshot_path: Path) -> str:
        log(f"Calling Ollama ({self.model}) at {self.host}:{self.port}...", verbose_only=True)

        # Encode image
        image_b64 = image_to_base64(screenshot_path)

        # Build request
        payload = {
            "model": self.model,
            "prompt": prompt,
            "images": [image_b64],
            "stream": False,
            "options": {
                "temperature": 0.1,
                "top_p": 0.9,
                "top_k": 40,
            }
        }

        url = f"{self.base_url}/api/generate"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode())
                return result.get("response", "")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Ollama request failed: {e}")


class CodexBackend(LLMBackend):
    """Backend using Codex CLI."""

    def call(self, prompt: str, screenshot_path: Path) -> str:
        log("Calling Codex CLI...", verbose_only=True)

        full_prompt = f"{prompt}\n\nAnalyze the attached screenshot and respond with ###OBS, ###THINK, and ###CMD blocks."

        result = subprocess.run(
            ["codex", "-p", full_prompt, "--image", str(screenshot_path)],
            capture_output=True, text=True
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
            capture_output=True, text=True
        )

        if result.returncode != 0:
            raise RuntimeError(f"claude failed: {result.stderr}")

        return result.stdout


def execute_command(cmd: str) -> Tuple[bool, str]:
    """Execute a single validated command."""
    log_file = LOG_DIR / f"cmd_{timestamp()}.out"

    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=30
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


def execute_commands(commands: List[str], delay: float = 0.3) -> List[Tuple[str, bool, str]]:
    """Execute a sequence of commands with delays between them."""
    results = []

    for i, cmd in enumerate(commands):
        log(f"  [{i+1}/{len(commands)}] {cmd}")
        success, result = execute_command(cmd)
        results.append((cmd, success, result))

        if not success:
            log(f"  Command failed, stopping sequence: {result}")
            break

        if i < len(commands) - 1:
            time.sleep(delay)

    return results


def select_model_interactive(backend: OllamaBackend) -> str:
    """Interactively select a model from available options."""
    models = backend.list_models()

    if not models:
        print("No models found. Please pull a vision model first:")
        print("  ollama pull llava")
        print("  ollama pull moondream")
        print("  ollama pull minicpm-v")
        sys.exit(1)

    print("\nAvailable vision models:")
    for i, model in enumerate(models, 1):
        print(f"  {i}. {model}")

    while True:
        try:
            choice = input(f"\nSelect model (1-{len(models)}): ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(models):
                return models[idx]
        except (ValueError, IndexError):
            pass
        print(f"Please enter a number between 1 and {len(models)}")


def get_backend(name: str, host: str = None, port: int = None, model: str = None) -> LLMBackend:
    """Factory function to create LLM backend."""
    if name == "ollama":
        host = host or os.environ.get("OLLAMA_HOST", "localhost")
        port = port or int(os.environ.get("OLLAMA_PORT", "11434"))
        backend = OllamaBackend(host, port, model)

        # Check connection
        if not backend.check_connection():
            print(f"Error: Cannot connect to Ollama at {host}:{port}")
            print("Make sure Ollama is running: ollama serve")
            sys.exit(1)

        # Select model if not specified
        if not model:
            model = select_model_interactive(backend)
            backend.model = model

        log(f"Using Ollama model: {model}")
        return backend

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
  %(prog)s -b ollama --model llava "Open Firefox and search for weather"
  %(prog)s -b ollama --host 192.168.1.100 --port 11434 "Open browser"
  %(prog)s -b claude "Open Safari and search for weather"
  %(prog)s -b codex "Click the Settings icon"
  %(prog)s --check-deps  # Check dependencies without running

Backends:
  ollama    Use Ollama API directly (default)
  codex     Use OpenAI Codex CLI
  claude    Use Claude CLI

Platforms:
  macOS     Uses cliclick, osascript, screencapture
  Linux     Uses ydotool/xdotool, grim/scrot
"""
    )
    parser.add_argument("goal", nargs="?", help="The goal for the agent to achieve")
    parser.add_argument("-b", "--backend", default="ollama",
                        choices=["ollama", "codex", "claude"],
                        help="LLM backend (default: ollama)")
    parser.add_argument("--host", default=None,
                        help="Ollama host (default: localhost, or OLLAMA_HOST env)")
    parser.add_argument("--port", type=int, default=None,
                        help="Ollama port (default: 11434, or OLLAMA_PORT env)")
    parser.add_argument("--model", default=None,
                        help="Ollama model to use (will prompt if not specified)")
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

    # Detect platform and display server
    plat = detect_platform()
    if plat == Platform.UNKNOWN:
        print(f"Error: Unsupported platform: {platform.system()}", file=sys.stderr)
        print("Supported platforms: macOS, Linux", file=sys.stderr)
        sys.exit(1)

    display_server = None
    input_tool = None

    log(f"Detected platform: {plat}")
    if plat == Platform.LINUX:
        display_server = detect_display_server()
        input_tool = detect_input_tool()
        log(f"Display server: {display_server}")
        log(f"Input tool: {input_tool or 'none found'}")

    # Check dependencies
    if not print_dependency_status(plat, args.backend, display_server):
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
        backend = get_backend(args.backend, args.host, args.port, args.model)
    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Get screen resolution
    width, height = get_screen_resolution(plat, display_server)
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
        take_screenshot(plat, display_server)

        # Build prompt with history context
        prompt = build_prompt(args.goal, history.get_context(), plat, width, height, input_tool)

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
        observation, reasoning, commands = extract_blocks(response)

        log(f"Observation: {observation[:100]}...")
        log(f"Reasoning: {reasoning[:100]}...")
        log(f"Commands ({len(commands)}): {commands}")

        # Check for goal achieved
        if "GOAL ACHIEVED" in observation.upper():
            log("GOAL ACHIEVED!")
            history.add_action(iteration, observation, reasoning, commands,
                             [(commands[0] if commands else "goal", True, "Goal completed")])
            history.mark_completed()
            break

        # Validate all commands before executing any
        all_valid, error, failed_idx = validate_commands(commands, plat, input_tool)
        if not all_valid:
            log(f"ERROR: Invalid command - {error}")
            shutil.move(str(MD_OUT), str(PAST_DIR / f"model_invalid_{timestamp()}.md"))
            history.add_action(iteration, observation, reasoning, commands,
                             [(commands[failed_idx] if failed_idx >= 0 else "N/A", False, f"REJECTED: {error}")])
            sys.exit(1)

        # Execute command sequence
        log(f"Executing {len(commands)} command(s)...")
        results = execute_commands(commands, delay=0.3)

        # Check if all succeeded
        all_succeeded = all(r[1] for r in results)
        if not all_succeeded:
            log(f"WARNING: Command sequence had failures")

        # Update history with all results
        history.add_action(iteration, observation, reasoning, commands, results)

        # Small delay for UI updates after command sequence
        time.sleep(1.0)

    else:
        log(f"WARNING: Reached maximum iterations ({args.max_iter}) without achieving goal")

    log(f"Agent finished. History saved to: {ACTION_HISTORY_FILE}")


if __name__ == "__main__":
    main()
