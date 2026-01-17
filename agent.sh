#!/usr/bin/env bash
set -euo pipefail

# agentvision - PC automation agent using screenshots and LLM
# Supports multiple backends: ssh-gpu (aiocr), codex, claude

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ACTION_HISTORY_FILE="$SCRIPT_DIR/action_history.json"
ACTION_HISTORY_TXT="$SCRIPT_DIR/action_history.txt"  # Human-readable version
SCREENSHOT="$SCRIPT_DIR/currentscreen.png"
MD_OUT="$SCRIPT_DIR/currentscreen.md"
PAST_DIR="$SCRIPT_DIR/past_screens"
LOG_DIR="$SCRIPT_DIR/logs"

# Defaults
BACKEND="ssh-gpu"  # ssh-gpu, codex, claude
SSH_HOST=""
SSH_PORT="25114"
GOAL=""
MAX_ITERATIONS=50
VERBOSE=0

mkdir -p "$PAST_DIR" "$LOG_DIR"

usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS] "GOAL"

PC automation agent using screenshots and LLM.

OPTIONS:
  -b, --backend BACKEND   LLM backend: ssh-gpu, codex, claude (default: ssh-gpu)
  -h, --ssh-host HOST     SSH host for ssh-gpu backend
  -p, --ssh-port PORT     SSH port for Ollama (default: 25114)
  -m, --max-iter N        Maximum iterations (default: 50)
  -r, --reset             Reset action history before starting
  -v, --verbose           Verbose output
  --help                  Show this help

BACKENDS:
  ssh-gpu   Use aiocr via SSH tunnel to GPU server with Ollama
            Requires: aiocr in PATH, SSH_GPU_HOST env or -h flag

  codex     Use OpenAI Codex via 'codex -p ...'
            Requires: codex CLI installed

  claude    Use Claude via 'claude' CLI
            Requires: claude CLI installed

EXAMPLES:
  $(basename "$0") -b claude "Open Safari and search for weather"
  $(basename "$0") -b ssh-gpu -h gpu.local "Open Gmail in Brave"
  $(basename "$0") -b codex "Click the Settings icon"

ENVIRONMENT:
  SSH_GPU_HOST   Default SSH host for ssh-gpu backend
EOF
  exit 0
}

timestamp() { date +%Y%m%d_%H%M%S; }

log() {
  echo "[$(date '+%H:%M:%S')] $*"
}

log_verbose() {
  [[ "$VERBOSE" -eq 1 ]] && log "$*"
}

# Parse arguments
RESET_HISTORY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    -b|--backend) BACKEND="$2"; shift 2 ;;
    -h|--ssh-host) SSH_HOST="$2"; shift 2 ;;
    -p|--ssh-port) SSH_PORT="$2"; shift 2 ;;
    -m|--max-iter) MAX_ITERATIONS="$2"; shift 2 ;;
    -r|--reset) RESET_HISTORY=1; shift ;;
    -v|--verbose) VERBOSE=1; shift ;;
    --help) usage ;;
    -*) echo "Unknown option: $1" >&2; exit 1 ;;
    *) GOAL="$1"; shift ;;
  esac
done

if [[ -z "$GOAL" ]]; then
  echo "Error: GOAL is required." >&2
  usage
fi

# Validate backend
case "$BACKEND" in
  ssh-gpu)
    SSH_HOST="${SSH_HOST:-${SSH_GPU_HOST:-}}"
    if [[ -z "$SSH_HOST" ]]; then
      echo "Error: ssh-gpu backend requires SSH_GPU_HOST env or -h flag" >&2
      exit 1
    fi
    if ! command -v aiocr &>/dev/null; then
      echo "Error: aiocr not found in PATH" >&2
      exit 1
    fi
    ;;
  codex)
    if ! command -v codex &>/dev/null; then
      echo "Error: codex not found in PATH" >&2
      exit 1
    fi
    ;;
  claude)
    if ! command -v claude &>/dev/null; then
      echo "Error: claude not found in PATH" >&2
      exit 1
    fi
    ;;
  *)
    echo "Error: Unknown backend '$BACKEND'. Use: ssh-gpu, codex, claude" >&2
    exit 1
    ;;
esac

# Initialize or reset action history (JSON format for structured tracking)
init_history() {
  if [[ "$RESET_HISTORY" -eq 1 ]] || [[ ! -f "$ACTION_HISTORY_FILE" ]]; then
    log "Initializing action history..."
    cat > "$ACTION_HISTORY_FILE" <<EOF
{
  "goal": "$GOAL",
  "started_at": "$(date -Iseconds)",
  "status": "in_progress",
  "iterations": 0,
  "actions": []
}
EOF
    # Human-readable version
    cat > "$ACTION_HISTORY_TXT" <<EOF
# Action History
Goal: $GOAL
Started: $(date)
Status: In Progress

## Actions
EOF
  fi
}

# Add action to history (JSON + text)
add_action_to_history() {
  local iteration="$1"
  local observation="$2"
  local reasoning="$3"
  local command="$4"
  local result="$5"

  # Update JSON history using Python for proper JSON handling
  python3 - "$ACTION_HISTORY_FILE" "$iteration" "$observation" "$reasoning" "$command" "$result" <<'PYEOF'
import json
import sys

file_path = sys.argv[1]
iteration = int(sys.argv[2])
observation = sys.argv[3]
reasoning = sys.argv[4]
command = sys.argv[5]
result = sys.argv[6]

with open(file_path, 'r') as f:
    history = json.load(f)

history['iterations'] = iteration
history['actions'].append({
    'iteration': iteration,
    'timestamp': __import__('datetime').datetime.now().isoformat(),
    'observation': observation,
    'reasoning': reasoning,
    'command': command,
    'result': result
})

with open(file_path, 'w') as f:
    json.dump(history, f, indent=2)
PYEOF

  # Also append to human-readable text
  cat >> "$ACTION_HISTORY_TXT" <<EOF

### Iteration $iteration ($(date '+%H:%M:%S'))
**Observation:** $observation
**Reasoning:** $reasoning
**Command:** \`$command\`
**Result:** $result
EOF
}

# Mark goal achieved
mark_goal_achieved() {
  python3 - "$ACTION_HISTORY_FILE" <<'PYEOF'
import json
import sys

file_path = sys.argv[1]
with open(file_path, 'r') as f:
    history = json.load(f)

history['status'] = 'completed'
history['completed_at'] = __import__('datetime').datetime.now().isoformat()

with open(file_path, 'w') as f:
    json.dump(history, f, indent=2)
PYEOF

  echo -e "\n## Goal Achieved at $(date)" >> "$ACTION_HISTORY_TXT"
}

# Build context from history for LLM (last N actions for context window)
build_history_context() {
  local max_actions=10

  python3 - "$ACTION_HISTORY_FILE" "$max_actions" <<'PYEOF'
import json
import sys

file_path = sys.argv[1]
max_actions = int(sys.argv[2])

with open(file_path, 'r') as f:
    history = json.load(f)

print(f"Goal: {history['goal']}")
print(f"Status: {history['status']}")
print(f"Total iterations so far: {history['iterations']}")
print()

actions = history['actions'][-max_actions:]
if actions:
    print(f"Recent actions (last {len(actions)}):")
    for a in actions:
        print(f"  [{a['iteration']}] {a['command']}")
        if a['result']:
            print(f"      Result: {a['result'][:100]}...")
        print(f"      Reasoning: {a['reasoning'][:150]}...")
else:
    print("No actions taken yet.")
PYEOF
}

rotate_file_if_exists() {
  local f="$1"
  local prefix="$2"
  if [[ -f "$f" ]]; then
    mv "$f" "$PAST_DIR/${prefix}_$(timestamp).${f##*.}"
  fi
}

# Build the prompt for the LLM
build_prompt() {
  local history_context
  history_context="$(build_history_context)"

  cat <<EOF
# Role
You are an autonomous screen agent controlling a Mac. You interact using **cliclick** for mouse/keyboard and **osascript** for AppleScript automation.

# Current Goal
$GOAL

# Screen Information
- Resolution: 1512 x 982 pixels
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
$history_context

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
  And include "GOAL ACHIEVED" at the start of ###OBS
EOF
}

# Extract blocks from model output
extract_blocks() {
  local md="$1"
  local obs_file="$2"
  local think_file="$3"
  local cmd_file="$4"

  # Extract OBS block
  awk '
    $0=="###OBS"{in=1;next}
    $0~/^###/{in=0}
    in{print}
  ' "$md" | sed '/^[[:space:]]*$/d' > "$obs_file"

  # Extract THINK block
  awk '
    $0=="###THINK"{in=1;next}
    $0~/^###/{in=0}
    in{print}
  ' "$md" | sed '/^[[:space:]]*$/d' > "$think_file"

  # Extract CMD block - first non-empty line only
  awk '
    $0=="###CMD"{in=1;next}
    $0~/^###/{in=0}
    in{print}
  ' "$md" | sed '/^[[:space:]]*$/d' | head -n 1 > "$cmd_file"
}

# Validate command for safety
validate_cmd() {
  local cmd
  cmd="$(cat "$1" || true)"

  if [[ -z "$cmd" ]]; then
    echo "ERROR: Empty command from model."
    return 1
  fi

  # Must start with cliclick or osascript
  if [[ "$cmd" != cliclick* ]] && [[ "$cmd" != osascript* ]]; then
    echo "ERROR: Command must start with 'cliclick' or 'osascript'. Got: $cmd"
    return 1
  fi

  # For osascript, allow -e flag but reject other suspicious patterns
  if [[ "$cmd" == osascript* ]]; then
    # Reject rm, sudo, curl, wget, etc in osascript
    if echo "$cmd" | grep -Eiq '(rm |sudo|curl|wget|kill|pkill|>|>>|\||;)'; then
      echo "ERROR: osascript contains forbidden patterns. Got: $cmd"
      return 1
    fi
  fi

  # For cliclick, reject shell metacharacters
  if [[ "$cmd" == cliclick* ]]; then
    if echo "$cmd" | grep -Eq '[;&|><`$()]'; then
      echo "ERROR: cliclick contains forbidden shell characters. Got: $cmd"
      return 1
    fi
  fi

  return 0
}

# Call LLM based on backend
call_llm() {
  local prompt="$1"
  local screenshot="$2"
  local output_file="$3"

  case "$BACKEND" in
    ssh-gpu)
      local aiocr_log="$LOG_DIR/aiocr_$(timestamp).log"
      log_verbose "Calling aiocr via SSH to $SSH_HOST..."

      # aiocr writes to currentscreen.md by default when given currentscreen.png
      if ! aiocr "$screenshot" -p "$prompt" -j 1 -H "$SSH_HOST" --port "$SSH_PORT" >"$aiocr_log" 2>&1; then
        echo "ERROR: aiocr failed. See: $aiocr_log"
        cat "$aiocr_log" >&2
        return 1
      fi

      # aiocr outputs to stdout which we captured, but also may write to .md
      # Check if MD_OUT was created, otherwise use the log
      if [[ -f "$MD_OUT" ]]; then
        cp "$MD_OUT" "$output_file"
      else
        # Extract just the model response from the log
        cp "$aiocr_log" "$output_file"
      fi
      ;;

    codex)
      log_verbose "Calling Codex CLI..."
      local codex_prompt
      # Codex can take image input - combine prompt with image reference
      codex_prompt="$prompt

[Screenshot attached: $screenshot]

Analyze the screenshot and respond with ###OBS, ###THINK, and ###CMD blocks."

      # Use codex with image support
      if ! codex -p "$codex_prompt" --image "$screenshot" > "$output_file" 2>&1; then
        echo "ERROR: codex failed."
        cat "$output_file" >&2
        return 1
      fi
      ;;

    claude)
      log_verbose "Calling Claude CLI..."
      # Claude CLI can accept images via stdin or file reference
      local claude_prompt
      claude_prompt="$prompt

Analyze the attached screenshot and respond with ###OBS, ###THINK, and ###CMD blocks."

      # Pass image to claude
      if ! claude --image "$screenshot" "$claude_prompt" > "$output_file" 2>&1; then
        echo "ERROR: claude failed."
        cat "$output_file" >&2
        return 1
      fi
      ;;
  esac

  return 0
}

# Main agent loop
main_loop() {
  init_history

  log "Starting agent with backend: $BACKEND"
  log "Goal: $GOAL"
  log "Max iterations: $MAX_ITERATIONS"
  echo

  local iter=0
  while [[ $iter -lt $MAX_ITERATIONS ]]; do
    iter=$((iter + 1))
    log "=== Iteration $iter ==="

    # Rotate old files
    rotate_file_if_exists "$SCREENSHOT" "screen"
    rotate_file_if_exists "$MD_OUT" "model"

    # Take screenshot
    log_verbose "Taking screenshot..."
    screencapture "$SCREENSHOT"

    # Build prompt with history context
    local prompt
    prompt="$(build_prompt)"

    # Call LLM
    log "Analyzing screen with $BACKEND..."
    local llm_output="$LOG_DIR/llm_output_$(timestamp).txt"
    if ! call_llm "$prompt" "$SCREENSHOT" "$llm_output"; then
      log "ERROR: LLM call failed"
      exit 1
    fi

    # Copy to MD_OUT for consistency
    cp "$llm_output" "$MD_OUT"

    # Extract response blocks
    local obs_tmp think_tmp cmd_tmp
    obs_tmp="$(mktemp)"
    think_tmp="$(mktemp)"
    cmd_tmp="$(mktemp)"

    extract_blocks "$MD_OUT" "$obs_tmp" "$think_tmp" "$cmd_tmp"

    local observation reasoning command
    observation="$(cat "$obs_tmp" | tr '\n' ' ' | sed 's/  */ /g')"
    reasoning="$(cat "$think_tmp" | tr '\n' ' ' | sed 's/  */ /g')"
    command="$(cat "$cmd_tmp")"

    log "Observation: ${observation:0:100}..."
    log "Reasoning: ${reasoning:0:100}..."
    log "Command: $command"

    # Check for goal achieved
    if echo "$observation" | grep -qi 'GOAL ACHIEVED'; then
      log "GOAL ACHIEVED!"
      add_action_to_history "$iter" "$observation" "$reasoning" "$command" "Goal completed"
      mark_goal_achieved
      rm -f "$obs_tmp" "$think_tmp" "$cmd_tmp"
      break
    fi

    # Validate command
    if ! validate_cmd "$cmd_tmp"; then
      log "ERROR: Invalid command. Saving debug info..."
      mv "$MD_OUT" "$PAST_DIR/model_invalid_$(timestamp).md"
      add_action_to_history "$iter" "$observation" "$reasoning" "$command" "REJECTED: Invalid command"
      rm -f "$obs_tmp" "$think_tmp" "$cmd_tmp"
      exit 1
    fi

    # Execute command
    local cmd_result=""
    local cmd_out="$LOG_DIR/cmd_$(timestamp).out"
    log "Executing: $command"
    if eval "$command" >"$cmd_out" 2>&1; then
      cmd_result="$(cat "$cmd_out" 2>/dev/null | head -c 200 || echo 'OK')"
      [[ -z "$cmd_result" ]] && cmd_result="OK"
    else
      cmd_result="FAILED: $(cat "$cmd_out" 2>/dev/null | head -c 200 || echo 'Unknown error')"
      log "WARNING: Command failed: $cmd_result"
    fi

    # Update history
    add_action_to_history "$iter" "$observation" "$reasoning" "$command" "$cmd_result"

    # Cleanup temp files
    rm -f "$obs_tmp" "$think_tmp" "$cmd_tmp"

    # Small delay for UI to update
    sleep 1.5
  done

  if [[ $iter -ge $MAX_ITERATIONS ]]; then
    log "WARNING: Reached maximum iterations ($MAX_ITERATIONS) without achieving goal"
  fi

  log "Agent finished. History saved to: $ACTION_HISTORY_FILE"
}

main_loop
