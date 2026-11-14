#!/usr/bin/env python3
"""PreToolUse guard for the role agents (docs/engineering/agent-team.md, OQ-02).

Usage: role-guard.py <role>   (hook input as JSON on stdin)

Denies file edits outside the role's paths and a fixed set of shell commands
that no agent may run. It is a guard against mistakes, not a security
boundary: a shell command can still write any file. The pull-request review
and the rule set on main are the controls behind it.
"""
import json
import os
import re
import subprocess
import sys

# Paths each role may edit, relative to the repository root.
ROLE_PATHS = {
    "architect": [
        "*.md",
        "docs/**/*.md",
        ".claude/agents/**",
        ".claude/hooks/**",
    ],
    "developer": [
        "core/**",
        "matter/**",
        "webui/**",
        "docs/api/**",
        "README.md",
        "CONTRIBUTING.md",
        "ARCHITECTURE.md",  # register rows in section 10 only; checked in review (CQ-R13)
    ],
    "devops": [
        ".github/**",
        "kas/**",
        "meta-nextoomio/**",
        "scripts/**",
        "Makefile",
        ".gitignore",
        "docs/evidence/**",
        "ARCHITECTURE.md",  # register rows in section 10 only; checked in review (CQ-R13)
    ],
    "reviewer": [],
    "qm": [],
}

# Paths that stay read-only for a role even when a pattern above matches.
ROLE_EXCLUDED = {
    "architect": ["docs/evidence/**", "docs/api/**"],
}

# Read-only for every role.
ALWAYS_EXCLUDED = ["SK/**", ".git/**"]

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

# Shell commands no agent runs. Merging, closing issues, and changing
# repository settings belong to the project owner.
DENIED_COMMANDS = [
    (r"\bgh\s+pr\s+merge\b", "merging is done by the project owner"),
    (r"\bgh\s+api\b[^|;&]*\bpulls/\d+/merge\b", "merging is done by the project owner"),
    (r"\bgh\s+issue\s+close\b", "issues are closed by the project owner"),
    (r"\bgh\s+issue\s+delete\b", "issues are not deleted by agents"),
    (r"\bgh\s+repo\s+(edit|delete|rename|archive)\b", "repository settings belong to the project owner"),
    (r"\bgh\s+api\b[^|;&]*\b(rulesets|branches/[^/\s]+/protection)\b[^|;&]*(-X|--method)\s*(PUT|POST|PATCH|DELETE)",
     "branch rules belong to the project owner"),
    (r"\bgit\s+push\b[^|;&]*(\s--force\b|\s--force-with-lease\b|\s-f\b)", "forced pushes are not allowed"),
    (r"\bgit\s+push\b[^|;&]*\s(\S+:)?(refs/heads/)?main\b", "main changes only through a pull request"),
]

# Only the Reviewer submits pull-request reviews.
REVIEW_COMMAND = r"\bgh\s+pr\s+review\b"


def deny(reason):
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "role-guard: " + reason,
            }
        },
        sys.stdout,
    )
    sys.exit(0)


def matches(path, pattern):
    """Glob match where '**' spans directories and '*' stays within one."""
    regex = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            regex += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            regex += ".*"
            i += 2
        elif pattern[i] == "*":
            regex += "[^/]*"
            i += 1
        else:
            regex += re.escape(pattern[i])
            i += 1
    return re.fullmatch(regex, path) is not None


def git(directory, *args):
    try:
        out = subprocess.run(
            ["git", "-C", directory, *args], capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return os.path.realpath(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def existing_dir(path):
    while path and not os.path.isdir(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path


def relative_path(file_path, cwd):
    """Path relative to the checkout or worktree of this repository that contains it, else None."""
    full = os.path.realpath(os.path.join(cwd, file_path))
    directory = existing_dir(os.path.dirname(full))
    root = git(directory, "rev-parse", "--show-toplevel")
    ours = git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    theirs = git(directory, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not root or not ours or ours != theirs:
        return None
    if full != root and not full.startswith(root + os.sep):
        return None
    return os.path.relpath(full, root).replace(os.sep, "/")


def check_edit(role, tool_input, cwd):
    file_path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not file_path:
        deny("edit without a file path")
    rel = relative_path(file_path, cwd)
    if rel is None:
        deny(f"'{file_path}' is outside the repository; role '{role}' edits only inside it")
    for pattern in ALWAYS_EXCLUDED + ROLE_EXCLUDED.get(role, []):
        if matches(rel, pattern):
            deny(f"'{rel}' is read-only for role '{role}'")
    allowed = list(ROLE_PATHS[role])
    # Paths a story names under "Touches" outside the role's defaults, set by
    # the project owner when starting the session.
    allowed += [p for p in os.environ.get("NEXTOOMIO_EXTRA_PATHS", "").split(":") if p]
    if not any(matches(rel, pattern) for pattern in allowed):
        deny(
            f"'{rel}' is outside the paths of role '{role}'. If the task needs it, stop and "
            "report this to the project owner instead of working around the guard."
        )


def check_command(role, tool_input):
    command = tool_input.get("command", "")
    for pattern, reason in DENIED_COMMANDS:
        if re.search(pattern, command):
            deny(reason)
    if role != "reviewer" and re.search(REVIEW_COMMAND, command):
        deny("only the Reviewer submits pull-request reviews")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ROLE_PATHS:
        deny("unknown role; expected one of " + ", ".join(sorted(ROLE_PATHS)))
    role = sys.argv[1]
    try:
        event = json.load(sys.stdin)
    except ValueError:
        deny("unreadable hook input")
    tool = event.get("tool_name", "")
    tool_input = event.get("tool_input") or {}
    cwd = event.get("cwd") or os.getcwd()
    if tool in EDIT_TOOLS:
        check_edit(role, tool_input, cwd)
    elif tool == "Bash":
        check_command(role, tool_input)
    sys.exit(0)


if __name__ == "__main__":
    main()
