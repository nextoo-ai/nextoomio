#!/bin/sh
# Start a Claude Code session as one role of the agent team, acting on GitHub
# as that role's GitHub App instead of the project owner's account.
#
# Usage: scripts/agent-run.sh <role> [claude arguments...]
#   scripts/agent-run.sh reviewer
#   scripts/agent-run.sh developer --worktree P0-F4.5-trial-deadline
#   scripts/agent-run.sh --check <role>     set up the identity, print it, and exit
#
# For the session it:
#   - gives gh and git a private configuration that holds only the App's
#     short-lived token and refreshes it while the session runs;
#   - sets the commit author to the App's bot identity;
#   - starts the role: Claude Code (`claude --agent <role>`) for architect,
#     reviewer, and qm; Antigravity (`agy`) for developer and devops.
# Nothing is written into the repository. See docs/engineering/agent-team.md.
set -eu

check=0
if [ "${1:-}" = "--check" ]; then check=1; shift; fi
role=${1:?usage: agent-run.sh [--check] <role> [claude arguments...]}
shift
case $role in
  architect|developer|reviewer|qm|devops) ;;
  *) echo "agent-run: unknown role '$role'" >&2; exit 2 ;;
esac

here=$(cd "$(dirname "$0")" && pwd)
state_dir=${NEXTOOMIO_STATE_DIR:-$HOME/.config/nextoomio}
gh_dir=$state_dir/gh/$role
bot="nextoomio-$role[bot]"

umask 077
mkdir -p "$gh_dir"

write_token() {
  token=$("$here/agent-token.sh" "$role") || return 1
  tmp=$gh_dir/hosts.yml.$$
  printf 'github.com:\n    oauth_token: %s\n    user: %s\n    git_protocol: https\n' "$token" "$bot" > "$tmp"
  mv "$tmp" "$gh_dir/hosts.yml"
  unset token
}
write_token || { echo "agent-run: could not obtain a token for '$role'" >&2; exit 1; }

# The owner's credentials must not leak into the session.
unset GH_TOKEN GITHUB_TOKEN GH_ENTERPRISE_TOKEN
export GH_CONFIG_DIR="$gh_dir"
export GH_PROMPT_DISABLED=1

# git asks gh for credentials, and gh now knows only the App's token.
export GIT_CONFIG_COUNT=2
export GIT_CONFIG_KEY_0=credential.helper GIT_CONFIG_VALUE_0=
export GIT_CONFIG_KEY_1=credential.helper GIT_CONFIG_VALUE_1='!gh auth git-credential'
export GIT_TERMINAL_PROMPT=0

bot_id=$(gh api "users/nextoomio-$role%5Bbot%5D" --jq .id) || {
  echo "agent-run: could not read the bot identity of '$role'" >&2; exit 1; }
export GIT_AUTHOR_NAME="$bot" GIT_COMMITTER_NAME="$bot"
export GIT_AUTHOR_EMAIL="$bot_id+$bot@users.noreply.github.com"
export GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"
export NEXTOOMIO_ROLE="$role"

if [ "$check" = 1 ]; then
  echo "role:        $role"
  echo "commits as:  $GIT_AUTHOR_NAME <$GIT_AUTHOR_EMAIL>"
  echo "repositories visible to the token:"
  gh api installation/repositories --jq '.repositories[].full_name' | sed 's/^/  /'
  rm -f "$gh_dir/hosts.yml"
  exit 0
fi

# Installation tokens last one hour; replace the token every 30 minutes.
(
  while sleep 1800; do write_token || echo "agent-run: token refresh failed" >&2; done
) &
refresher=$!
cleanup() {
  kill "$refresher" 2>/dev/null || true
  rm -f "$gh_dir/hosts.yml"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# Which program runs the role (docs/engineering/agent-team.md, section 4).
# Author roles run in Antigravity on a Gemini model; the others in Claude Code.
case $role in
  developer|devops) engine=${NEXTOOMIO_ENGINE:-antigravity} ;;
  *)                engine=${NEXTOOMIO_ENGINE:-claude} ;;
esac

if [ "$engine" = claude ]; then
  claude --agent "$role" "$@"
  exit $?
fi

# Antigravity has no agent definitions or hooks of ours: the role's brief is
# the body of its definition, passed with the task. Only the headless form
# `agent-run.sh <role> -p "<task>"` is supported for it.
if [ "${1:-}" != "-p" ] || [ -z "${2:-}" ]; then
  echo "agent-run: role '$role' runs in Antigravity; use: agent-run.sh $role -p \"<task>\"" >&2
  exit 2
fi
model=${NEXTOOMIO_AGY_MODEL:-gemini-3.8-flash-high}
brief=$(awk 'f>=2; /^---$/ {f++}' "$here/../.claude/agents/$role.md")
prompt="$brief

Your model id for the role and model line is: $model (Antigravity).
The role-guard hook does not run in this program; the limits above apply all the same.

## Task

$2"
agy -p "$prompt" --model "$model" --mode accept-edits \
  --print-timeout "${NEXTOOMIO_AGENT_TIMEOUT:-45m}" < /dev/null
