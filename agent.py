#!/usr/bin/env python3
"""
AgentKVM - PC automation agent using screenshots and LLM
Supports multiple backends: ssh-gpu (aiocr), codex, claude
"""

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

SCRIPT_DIR = Path(__file__).parent.resolve()
ACTION_HISTORY_FILE = SCRIPT_DIR / "action_history.json"
ACTION_HISTORY_TXT = SCRIPT_DIR / "action_history.txt"
SCREENSHOT = SCRIPT_DIR / "currentscreen.png"
MD_OUT = SCRIPT_DIR / "currentscreen.md"
PAST_DIR = SCRIPT_DIR / "past_screens"
LOG_DIR = SCRIPT_DIR / "logs"

# Screen resolution (macOS default, can be overridden)
SCREEN_WIDTH = 1512
SCREEN_HEIGHT = 982


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def log(msg: str, verbose_only: bool = False):
    """Print timestamped log message."""
    global VERBOSE
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


def take_screenshot() -> Path:
    """Capture screen using macOS screencapture."""
    subprocess.run(["screencapture", str(SCREENSHOT)], check=True)
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
            # Update goal if different
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

        # Init text version
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
        """Add an action to history."""
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

        # Append to text version
        with open(self.txt_path, "a") as f:
            f.write(f"\n### Iteration {iteration} ({datetime.now().strftime('%H:%M:%S')})\n")
            f.write(f"**Observation:** {observation}\n")
            f.write(f"**Reasoning:** {reasoning}\n")
            f.write(f"**Command:** `{command}`\n")
            f.write(f"**Result:** {result}\n")

    def mark_completed(self):
        """Mark goal as achieved."""
        self.data["status"] = "completed"
        self.data["completed_at"] = datetime.now().isoformat()
        self._save()

        with open(self.txt_path, "a") as f:
            f.write(f"\n## Goal Achieved at {datetime.now()}\n")

    def get_context(self, max_actions: int = 10) -> str:
        """Build context string from recent actions."""
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


def build_prompt(goal: str, history_context: str) -> str:
    """Build the prompt for the LLM."""
    return f"""# Role
You are an autonomous screen agent controlling a Mac. You interact using **cliclick** for mouse/keyboard and **osascript** for AppleScript automation.

# Current Goal
{goal}

# Screen Information
- Resolution: {SCREEN_WIDTH} x {SCREEN_HEIGHT} pixels
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


def extract_blocks(text: str) -> Tuple[str, str, str]:
    """Extract OBS, THINK, CMD blocks from model output."""
    obs = think = cmd = ""

    # Extract OBS
    match = re.search(r'###OBS\s*\n(.*?)(?=###|$)', text, re.DOTALL)
    if match:
        obs = match.group(1).strip()

    # Extract THINK
    match = re.search(r'###THINK\s*\n(.*?)(?=###|$)', text, re.DOTALL)
    if match:
        think = match.group(1).strip()

    # Extract CMD - first non-empty line only
    match = re.search(r'###CMD\s*\n(.*?)(?=###|$)', text, re.DOTALL)
    if match:
        lines = [l.strip() for l in match.group(1).strip().split('\n') if l.strip()]
        if lines:
            cmd = lines[0]

    return obs, think, cmd


def validate_command(cmd: str) -> Tuple[bool, str]:
    """Validate command for safety. Returns (is_valid, error_message)."""
    if not cmd:
        return False, "Empty command"

    if not (cmd.startswith("cliclick") or cmd.startswith("osascript")):
        return False, f"Command must start with 'cliclick' or 'osascript'. Got: {cmd}"

    # For osascript, reject dangerous patterns
    if cmd.startswith("osascript"):
        dangerous = ["rm ", "sudo", "curl", "wget", "kill", "pkill", ">>", ">", "|", ";"]
        for d in dangerous:
            if d in cmd.lower():
                return False, f"osascript contains forbidden pattern '{d}'"

    # For cliclick, reject shell metacharacters
    if cmd.startswith("cliclick"):
        if re.search(r'[;&|><`$()]', cmd):
            return False, f"cliclick contains forbidden shell characters"

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

        if not shutil.which("aiocr"):
            raise RuntimeError("aiocr not found in PATH")

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

        # Check if MD_OUT was created
        if MD_OUT.exists():
            return MD_OUT.read_text()

        return result.stdout


class CodexBackend(LLMBackend):
    """Backend using Codex CLI."""

    def __init__(self):
        if not shutil.which("codex"):
            raise RuntimeError("codex not found in PATH")

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

    def __init__(self):
        if not shutil.which("claude"):
            raise RuntimeError("claude not found in PATH")

    def call(self, prompt: str, screenshot_path: Path) -> str:
        log("Calling Claude CLI...", verbose_only=True)

        full_prompt = f"""{prompt}

Analyze the attached screenshot and respond with ###OBS, ###THINK, and ###CMD blocks.
IMPORTANT: Output ONLY the three blocks (###OBS, ###THINK, ###CMD) with no other text."""

        # Claude CLI supports reading images
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
        description="PC automation agent using screenshots and LLM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s -b claude "Open Safari and search for weather"
  %(prog)s -b ssh-gpu --ssh-host gpu.local "Open Gmail in Brave"
  %(prog)s -b codex "Click the Settings icon"

Backends:
  ssh-gpu   Use aiocr via SSH tunnel to GPU server with Ollama
  codex     Use OpenAI Codex CLI
  claude    Use Claude CLI
"""
    )
    parser.add_argument("goal", help="The goal for the agent to achieve")
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

    args = parser.parse_args()
    VERBOSE = args.verbose

    ensure_dirs()

    # Initialize backend
    try:
        backend = get_backend(args.backend, args.ssh_host, args.ssh_port)
    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

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
        take_screenshot()

        # Build prompt with history context
        prompt = build_prompt(args.goal, history.get_context())

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
        is_valid, error = validate_command(command)
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
