#!/usr/bin/env python3
"""Run one ready issue through the agent team without a person in the loop.

Usage: scripts/agent-pipeline.py <issue number> [--role developer|devops]
       scripts/agent-pipeline.py --test

Steps (docs/engineering/agent-team.md, section 5):
  1. The author role implements the issue in the working tree. The pipeline
     checks that the changed paths belong to the role, then commits, pushes,
     and opens the pull request under the role's identity. (The author roles
     run in Codex or Antigravity, whose sandboxes do not let git write to .git.)
  2. Auto-merge is enabled, so GitHub merges once the rules on main are met.
  3. CI runs; the Reviewer reviews. Requested changes or a CI failure go back
     to the author, at most MAX_ROUNDS times, then the issue is blocked.
  4. After the merge the issue gets state:implemented and QM gives a verdict.
  5. On "QM-Verdict: PASS" the acceptance criteria are ticked and the issue
     is closed as completed; otherwise it stays open.

The script decides nothing itself: it starts roles and acts on what GitHub
and the roles report. Logs go to ~/.config/nextoomio/runs/, never into the
repository. Exit status: 0 closed, 1 error, 2 blocked or left open.
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from typing import NamedTuple

REPO = os.environ.get("NEXTOOMIO_REPO", "nextoo-ai/nextoomio")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MAX_ROUNDS = 2
DEVOPS_AREAS = {"area:ci", "area:build", "area:image", "area:tooling", "area:hardware"}
REQUIRED_CHECKS = None


def get_required_checks(repo=REPO, role=None):
    """Read the required check names for main from the rules API (D-048)."""
    try:
        rules = gh_json(["api", f"repos/{repo}/rules/branches/main"], role=role)
    except Exception as exc:
        raise Stop(1, f"cannot read the rules on main: {exc}")
    if not isinstance(rules, list):
        raise Stop(1, "cannot read the rules on main: unexpected response format")
    checks = set()
    has_rule = False
    for rule in rules:
        if isinstance(rule, dict) and rule.get("type") == "required_status_checks":
            has_rule = True
            params = rule.get("parameters") or {}
            for item in params.get("required_status_checks") or []:
                name = item.get("context") or item.get("name")
                if name:
                    checks.add(name)
    if not has_rule or not checks:
        raise Stop(1, "the rules on main name no required status checks")
    return checks
def author_model(env=os.environ) -> str:
    """Model id for the author roles' role-and-model line, with the same engine
    selection and defaults as scripts/agent-run.sh (D-068): Codex unless
    NEXTOOMIO_ENGINE names Antigravity or Claude Code."""
    engine = env.get("NEXTOOMIO_ENGINE", "codex")
    if engine == "codex":
        return env.get("NEXTOOMIO_CODEX_MODEL", "gpt-6.1-terra") + " (Codex)"
    if engine == "antigravity":
        return env.get("NEXTOOMIO_AGY_MODEL", "gemini-3.8-flash-high") + " (Antigravity)"
    if engine == "claude":
        # `claude --agent <role>` uses the model of the role's definition (.claude/agents/).
        return env.get("NEXTOOMIO_CLAUDE_MODEL", "opus") + " (Claude Code)"
    # agent-run.sh refuses an unknown engine with exit 2; the line never names a program that did not run.
    return f"unknown engine '{engine}'"


AUTHOR_MODEL = author_model()
AGENT_TIMEOUT = int(os.environ.get("NEXTOOMIO_AGENT_TIMEOUT_SECONDS", "3600"))


class Tooling(NamedTuple):
    run_dir: str
    tooling_dir: str
    agent_run: str
    agent_token: str
    role_guard: str
    agents_dir: str


TOOLING: Tooling | None = None


def pin_tooling(run_dir, root=ROOT):
    """Copy the tooling from root into run_dir outside the repository (D-040).

    Copies the session script, the token script, the guard, and the agent
    definitions while on main, so unreviewed changes on a branch cannot alter
    how the pipeline starts roles, obtains tokens, or checks paths.
    """
    tooling_dir = os.path.join(run_dir, "tooling")
    scripts_dir = os.path.join(tooling_dir, "scripts")
    hooks_dir = os.path.join(tooling_dir, ".claude", "hooks")
    agents_dir = os.path.join(tooling_dir, ".claude", "agents")
    os.makedirs(scripts_dir, exist_ok=True)
    os.makedirs(hooks_dir, exist_ok=True)
    os.makedirs(agents_dir, exist_ok=True)

    agent_run = os.path.join(scripts_dir, "agent-run.sh")
    agent_token = os.path.join(scripts_dir, "agent-token.sh")
    role_guard = os.path.join(hooks_dir, "role-guard.py")

    shutil.copy2(os.path.join(root, "scripts", "agent-run.sh"), agent_run)
    shutil.copy2(os.path.join(root, "scripts", "agent-token.sh"), agent_token)
    shutil.copy2(os.path.join(root, ".claude", "hooks", "role-guard.py"), role_guard)
    os.chmod(agent_run, 0o755)
    os.chmod(agent_token, 0o755)
    os.chmod(role_guard, 0o755)

    src_agents = os.path.join(root, ".claude", "agents")
    if os.path.isdir(src_agents):
        for name in os.listdir(src_agents):
            if name.endswith(".md"):
                shutil.copy2(os.path.join(src_agents, name), os.path.join(agents_dir, name))

    return Tooling(
        run_dir=run_dir,
        tooling_dir=tooling_dir,
        agent_run=agent_run,
        agent_token=agent_token,
        role_guard=role_guard,
        agents_dir=agents_dir,
    )


def is_app(login, role):
    """GitHub reports an App as 'nextoomio-x', 'app/nextoomio-x', or 'nextoomio-x[bot]'."""
    name = (login or "").removeprefix("app/").removesuffix("[bot]")
    return name == f"nextoomio-{role}"


class Stop(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def run(cmd, env=None, check=True, capture=True, log=None, cwd=None):
    out = subprocess.run(cmd, cwd=cwd or ROOT, env=env, text=True,
                         stdout=subprocess.PIPE if capture else None,
                         stderr=subprocess.STDOUT if capture else None)
    if log and out.stdout:
        with open(log, "a") as f:
            f.write(out.stdout)
    if check and out.returncode != 0:
        raise Stop(1, f"command failed ({out.returncode}): {' '.join(cmd[:4])} ...\n{(out.stdout or '')[-800:]}")
    return out


def token(role, cwd=None):
    script = TOOLING.agent_token if TOOLING else os.path.join(HERE, "agent-token.sh")
    return run([script, role], cwd=cwd).stdout.strip()


def gh(args, role=None, check=True):
    env = dict(os.environ)
    if role:
        env["GH_TOKEN"] = token(role)
    return run(["gh"] + args + ([] if "--repo" in args or args[0] == "api" else ["--repo", REPO]),
               env=env, check=check).stdout


def gh_json(args, role=None):
    return json.loads(gh(args, role=role))


def say(message):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {message}", flush=True)


def agent(role, task, log, extra_paths=None, cwd=None):
    """Start one role headlessly and wait for it."""
    env = dict(os.environ)
    if extra_paths:
        env["NEXTOOMIO_EXTRA_PATHS"] = ":".join(extra_paths)
    if TOOLING:
        env["NEXTOOMIO_AGENTS_DIR"] = TOOLING.agents_dir
    script = TOOLING.agent_run if TOOLING else os.path.join(HERE, "agent-run.sh")
    cmd = [script, role, "-p", task]
    if role in ("reviewer", "qm", "architect"):
        cmd += ["--permission-mode", "auto"]
    say(f"{role}: started")
    try:
        out = subprocess.run(cmd, cwd=cwd or ROOT, env=env, text=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=AGENT_TIMEOUT)
    except subprocess.TimeoutExpired as expired:
        with open(log, "a") as f:
            f.write(f"\n===== {role}: timed out =====\n{expired.stdout or ''}\n")
        raise Stop(1, f"{role} did not finish within {AGENT_TIMEOUT // 60} minutes")
    with open(log, "a") as f:
        f.write(f"\n===== {role} @ {datetime.datetime.now().isoformat()} =====\n{out.stdout or ''}\n")
    say(f"{role}: finished (exit {out.returncode})")
    if out.returncode != 0:
        raise Stop(1, f"{role} session failed with exit status {out.returncode}; see log {log}")
    return out.stdout or ""


def bot_identity(role):
    bot = f"nextoomio-{role}[bot]"
    bot_id = gh(["api", f"users/nextoomio-{role}%5Bbot%5D", "--jq", ".id"]).strip()
    return bot, f"{bot_id}+{bot}@users.noreply.github.com"


def changed_paths(cwd=None):
    out = run(["git", "status", "--porcelain", "-uall"], cwd=cwd).stdout
    return [line[3:].split(" -> ")[-1].strip('"') for line in out.splitlines() if line.strip()]


def outside_role_paths(role, paths, extra_paths, cwd=None):
    """Paths the role may not edit, judged by the same guard the Claude Code roles run under."""
    repo_root = cwd or ROOT
    env = dict(os.environ, NEXTOOMIO_EXTRA_PATHS=":".join(extra_paths or []))
    guard = TOOLING.role_guard if TOOLING else os.path.join(ROOT, ".claude", "hooks", "role-guard.py")
    refused = []
    for path in paths:
        event = json.dumps({"tool_name": "Write", "cwd": repo_root,
                            "tool_input": {"file_path": os.path.join(repo_root, path)}})
        out = subprocess.run([sys.executable, guard, role], input=event, text=True,
                             capture_output=True, env=env)
        if out.stdout.strip():
            refused.append(path)
    return refused


def replace_commit_marker_in_text(text: str, commit_hash: str) -> tuple[str, int]:
    """Replace {{COMMIT}} only in the 'Image version and commit' field line (D-039).

    Preserves occurrences of {{COMMIT}} in prose (e.g. in Procedure or Observed result).
    Returns (new_text, replacement_count).
    """
    field_prefix_re = re.compile(
        r"^(\s*(?:[-*]\s+)?(?:\*\*)?(?:Image(?:\s+version)?(?:\s+and\s+commit)?|(?:Check\s+)?Commit)(?::\*\*|\*\*:|:)\s*)(.*?)(\r?\n)?$",
        re.IGNORECASE,
    )
    lines = text.splitlines(keepends=True)
    new_lines = []
    count = 0
    for line in lines:
        m = field_prefix_re.match(line)
        if m and "{{COMMIT}}" in m.group(2):
            new_value = m.group(2).replace("{{COMMIT}}", commit_hash)
            count += m.group(2).count("{{COMMIT}}")
            trailing_nl = m.group(3) or ""
            new_lines.append(f"{m.group(1)}{new_value}{trailing_nl}")
        else:
            new_lines.append(line)
    return "".join(new_lines), count


def replace_commit_markers(paths, commit_hash, root=ROOT):
    """Replace {{COMMIT}} only in 'Image version and commit' field of changed evidence files (D-039).

    Preserves occurrences of {{COMMIT}} in prose.
    Returns the list of relative file paths that were updated.
    """
    updated_files = []
    for p in paths:
        if (p.startswith("docs/evidence/") and p.endswith(".md")
                and p not in ("docs/evidence/TEMPLATE.md", "docs/evidence/README.md")):
            full_path = os.path.join(root, p)
            if os.path.isfile(full_path):
                with open(full_path, "r", encoding="utf-8") as f:
                    content = f.read()
                new_content, count = replace_commit_marker_in_text(content, commit_hash)
                if count > 0:
                    with open(full_path, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    updated_files.append(p)
    return updated_files


def update_evidence_commit_markers(paths, change_commit, issue, env=None, root=ROOT):
    """Replace {{COMMIT}} in changed evidence files and record in a follow-up commit (D-039).

    Returns the list of updated evidence file paths, or [] if none contained the marker.
    """
    updated = replace_commit_markers(paths, change_commit, root=root)
    if updated:
        for p in updated:
            run(["git", "add", p], env=env, cwd=root)
        run(["git", "commit", "-q", "-m", f"Record change commit in evidence\n\nRefs #{issue}"],
            env=env, cwd=root)
    return updated


def clean_author_summary(summary: str, issue: int) -> str:
    """Remove from the summary every line that is only 'Refs #<issue>' and every line that starts with 'Role:' (D-049)."""
    issue_pattern = re.compile(rf"^\s*Refs\s+#{issue}\s*$", re.IGNORECASE)
    cleaned_lines = []
    for line in summary.splitlines():
        if issue_pattern.match(line):
            continue
        if line.strip().startswith("Role:"):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def build_pr_body(role: str, issue: int, summary: str) -> str:
    """Build the pull-request description with clean summary, single Refs line, and single Role line (D-049)."""
    clean = clean_author_summary(summary, issue)
    role_name = "DevOps" if role == "devops" else "Developer"
    return (f"Refs #{issue}\n\n{clean}\n\nThe branch, commit, and this pull request were made by the "
            f"pipeline from the author's working tree, after checking the changed paths against the role.\n\n"
            f"Role: {role_name} · Model: {AUTHOR_MODEL}")


def build_round_comment(role: str, issue: int, summary: str) -> str:
    """Build the round comment with clean summary and single Role line (D-049)."""
    clean = clean_author_summary(summary, issue)
    role_name = "DevOps" if role == "devops" else "Developer"
    return f"{clean}\n\nRole: {role_name} · Model: {AUTHOR_MODEL}"


def round_author_task(number: int, issue: int, why: str) -> str:
    """Build the task given to an author role on a later round (D-054)."""
    return (f"Pull request {number} for issue {issue} needs changes: {why}. "
            f"Read the review and the CI result with `gh pr view {number} --comments` and "
            f"`gh pr checks {number}`, and address them. "
            f"Provide a PR-Summary that describes the whole pull request at its head "
            f"(what the diff contains now, what was run on it), not only the changes of the round. "
            f"Follow your role definition.")


def conflict_author_task(number: int, issue: int) -> str:
    """Build the task given to an author role when pull request conflicts with main (D-062)."""
    return (f"Pull request {number} for issue {issue} conflicts with main. "
            f"Resolve the conflicts in favor of the behavior on main plus the story's own change, "
            f"and then address any open review with `gh pr view {number} --comments`. "
            f"Provide a PR-Summary that describes the whole pull request at its head "
            f"(what the diff contains now, what was run on it), not only the changes of the round. "
            f"Follow your role definition.")


def try_merge_main(branch: str, role: str, cwd=None) -> str:
    """Attempt to merge origin/main into branch (D-062).

    Returns:
      'UP_TO_DATE': branch already includes origin/main.
      'CLEAN_MERGE': cleanly merged and pushed without an author round.
      'CONFLICT': merge failed with conflicts; conflict markers left in tree.
    """
    bot, email = bot_identity(role)
    env = dict(os.environ, GIT_AUTHOR_NAME=bot, GIT_COMMITTER_NAME=bot,
               GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_EMAIL=email)
    head_before = run(["git", "rev-parse", "HEAD"], cwd=cwd).stdout.strip()
    res = run(["git", "merge", "--no-edit", "origin/main"], env=env, cwd=cwd, check=False)
    if res.returncode == 0:
        head_after = run(["git", "rev-parse", "HEAD"], cwd=cwd).stdout.strip()
        if head_before == head_after:
            return "UP_TO_DATE"
        helper = "!f() { echo username=x-access-token; echo \"password=$NEXTOOMIO_PUSH_TOKEN\"; }; f"
        run(["git", "-c", "credential.helper=", "-c", f"credential.helper={helper}",
             "push", "-q", "-u", "origin", branch],
            env=dict(env, NEXTOOMIO_PUSH_TOKEN=token(role, cwd=cwd)), cwd=cwd)
        say(f"clean merge with origin/main pushed on {branch}")
        return "CLEAN_MERGE"
    return "CONFLICT"


def deliver(role, issue, title, branch, output, extra_paths, number, cwd=None):
    """Commit and push the author's changes as the role; open the pull request if there is none.

    Returns the pull request number, or None when the author changed nothing.
    """
    paths = changed_paths(cwd=cwd)
    if not paths:
        return None
    paths_to_judge = paths
    merge_in_progress = run(["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=cwd, check=False).returncode == 0
    if merge_in_progress:
        run(["git", "add", "-A"], cwd=cwd)
        paths = changed_paths(cwd=cwd)
        ref = "origin/main" if run(["git", "rev-parse", "-q", "--verify", "origin/main"], cwd=cwd, check=False).returncode == 0 else "MERGE_HEAD"
        paths_to_judge = [
            p for p in paths
            if run(["git", "diff", "--quiet", ref, "--", p], cwd=cwd, check=False).returncode != 0
        ]
    refused = outside_role_paths(role, paths_to_judge, extra_paths, cwd=cwd)
    if refused:
        run(["git", "stash", "push", "-u", "-m", f"pipeline: refused changes for issue {issue}"], cwd=cwd)
        run(["git", "switch", "main"], cwd=cwd)
        block(issue, f"the {role} changed paths outside its role, so nothing was committed: "
                     + ", ".join(f"`{p}`" for p in refused) + ". The changes are kept in a local stash.")
    found = re.search(r"^PR-Title:\s*(.+)$", output, re.M)
    pr_title = found.group(1).strip() if found else title
    matches = list(re.finditer(r"^PR-Summary:\s*", output, re.M))
    if matches:
        summary_raw = output[matches[-1].end():].strip()
        has_summary = bool(summary_raw)
        summary = summary_raw if has_summary else "The author gave no summary; see the diff."
    else:
        has_summary = False
        summary = "The author gave no summary; see the diff."
    bot, email = bot_identity(role)
    env = dict(os.environ, GIT_AUTHOR_NAME=bot, GIT_COMMITTER_NAME=bot,
               GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_EMAIL=email)
    run(["git", "add", "-A"], env=env, cwd=cwd)
    run(["git", "commit", "-q", "-m", f"{pr_title}\n\nRefs #{issue}"], env=env, cwd=cwd)

    # D-039: Replace {{COMMIT}} marker in changed evidence files with the full change commit hash.
    change_commit = run(["git", "rev-parse", "HEAD"], cwd=cwd).stdout.strip()
    update_evidence_commit_markers(paths, change_commit, issue, env=env, root=cwd or ROOT)

    helper = "!f() { echo username=x-access-token; echo \"password=$NEXTOOMIO_PUSH_TOKEN\"; }; f"
    run(["git", "-c", "credential.helper=", "-c", f"credential.helper={helper}",
         "push", "-q", "-u", "origin", branch], env=dict(env, NEXTOOMIO_PUSH_TOKEN=token(role, cwd=cwd)), cwd=cwd)
    if number:
        if has_summary:
            gh(["pr", "edit", str(number), "--body", build_pr_body(role, issue, summary)], role=role)
        gh(["pr", "comment", str(number), "--body",
            build_round_comment(role, issue, summary)], role=role)
        return number
    body = build_pr_body(role, issue, summary)
    url = gh(["pr", "create", "--base", "main", "--head", branch, "--title", pr_title, "--body", body],
             role=role).strip().splitlines()[-1]
    return int(url.rstrip("/").rsplit("/", 1)[-1])


# Upper bound for PR search queries (D-052). gh pr list defaults to 30; an explicit
# high limit ensures that pull requests merely mentioning the issue do not push
# the target pull request past the default limit.
PR_SEARCH_LIMIT = 300


def find_pr(issue, role):
    prs = gh_json(["pr", "list", "--state", "open", "--search", f"Refs #{issue}",
                   "--limit", str(PR_SEARCH_LIMIT),
                   "--json", "number,body,author,headRefName,headRefOid"])
    pattern = re.compile(rf"\bRefs\s+#{issue}\b")
    for pr in prs:
        if is_app((pr.get("author") or {}).get("login", ""), role) and pattern.search(pr.get("body") or ""):
            return pr
    return None


def find_merged_pr(issue, role):
    prs = gh_json(["pr", "list", "--state", "merged", "--search", f"Refs #{issue}",
                   "--limit", str(PR_SEARCH_LIMIT),
                   "--json", "number,body,author,headRefName,headRefOid"])
    pattern = re.compile(rf"\bRefs\s+#{issue}\b")
    for pr in prs:
        if is_app((pr.get("author") or {}).get("login", ""), role) and pattern.search(pr.get("body") or ""):
            return pr
    return None


def choose_branch(branch, pr=None, cwd=None):
    """Choose and set up the branch for working on an issue (D-042, D-046).

    Runs git fetch --prune origin before inspecting or setting refs.
    With no open pull request for the issue, creates or resets the local branch
    from origin/main. If the branch name already exists on origin, picks the
    next free name with a numeric suffix (-2, -3, ...).
    With an open pull request, sets the local branch to the
    pull request's head on origin.
    """
    run(["git", "fetch", "--prune", "origin"], cwd=cwd)
    if pr:
        target_branch = pr.get("headRefName") or branch
        run(["git", "switch", "-C", target_branch, f"origin/{target_branch}"], cwd=cwd)
        return target_branch
    candidate = branch
    suffix = 2
    while run(["git", "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{candidate}"],
              cwd=cwd, check=False).returncode == 0:
        candidate = f"{branch}-{suffix}"
        suffix += 1
    run(["git", "switch", "-C", candidate, "origin/main"], cwd=cwd)
    return candidate


def pr_state(number):
    return gh_json(["pr", "view", str(number), "--json",
                    "state,headRefOid,reviewDecision,mergeStateStatus,mergeable,reviews,statusCheckRollup,body"])


def wait_for_ci(number, required_checks=None, issue=None, timeout=3600):
    """Wait until every check has completed and required checks passed on the head commit (D-062)."""
    if required_checks is None:
        required_checks = REQUIRED_CHECKS if REQUIRED_CHECKS is not None else get_required_checks()
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = pr_state(number)
        if (str(state.get("mergeStateStatus") or "").upper() == "DIRTY"
                or str(state.get("mergeable") or "").upper() == "CONFLICTING"):
            target_issue = issue
            if target_issue is None:
                m = re.search(r"\bRefs\s+#(\d+)\b", state.get("body") or "")
                if m:
                    target_issue = int(m.group(1))
            if target_issue is not None:
                block(target_issue, f"pull request #{number} conflicts with main.")
            raise Stop(2, f"pull request #{number} conflicts with main")
        checks = state.get("statusCheckRollup") or []
        states = {c.get("name") or c.get("context"): c.get("conclusion") or c.get("status") or c.get("state")
                  for c in checks}
        if any(s in ("FAILURE", "CANCELLED", "TIMED_OUT", "ERROR", "ACTION_REQUIRED", "STARTUP_FAILURE")
               for s in states.values()):
            return False
        all_required_success = all(states.get(name) == "SUCCESS" for name in required_checks)
        all_completed = not any(s in ("IN_PROGRESS", "QUEUED", "PENDING", "WAITING", "REQUESTED", "EXPECTED")
                                for s in states.values())
        if all_required_success and all_completed:
            return True
        time.sleep(20)
    raise Stop(1, f"the checks on pull request #{number} did not finish within {timeout // 60} minutes")


def latest_review(number, head):
    """State and body of the Reviewer's last verdict on the head commit, or (None, '')."""
    reviews = [r for r in pr_state(number)["reviews"]
               if is_app(r["author"]["login"], "reviewer") and r["commit"]["oid"] == head
               and r["state"] in ("APPROVED", "CHANGES_REQUESTED")]
    return (reviews[-1]["state"], reviews[-1]["body"] or "") if reviews else (None, "")


def wait_for_merge(number, timeout=900):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pr_state(number)["state"] == "MERGED":
            return True
        time.sleep(15)
    return False


def block(issue, reason):
    gh(["issue", "edit", str(issue), "--remove-label", "state:ready", "--add-label", "state:blocked"],
       role="architect", check=False)
    gh(["issue", "comment", str(issue), "--body",
        f"Blocked by the pipeline: {reason}\n\nRole: Architect (pipeline) · Model: none"], role="architect")
    raise Stop(2, f"issue #{issue} blocked: {reason}")


def run_qm_and_close(issue, number, log, run_dir, cwd=None):
    commit = run(["git", "rev-parse", "main"], cwd=cwd).stdout.strip()
    started = datetime.datetime.now(datetime.timezone.utc)
    agent("qm", f"Verify issue {issue}, which now has state:implemented; its pull request {number} is merged. "
                f"Verify main at commit {commit} and include a standalone line "
                f"Verified-Commit: {commit} in your verdict. Follow your role definition.", log, cwd=cwd)
    comments = gh_json(["issue", "view", str(issue), "--json", "comments"])["comments"]
    fresh = []
    for comment in comments:
        if not is_app(comment["author"]["login"], "qm"):
            continue
        try:
            created = datetime.datetime.fromisoformat(comment["createdAt"].replace("Z", "+00:00"))
            if created.tzinfo is not None and created > started:
                fresh.append((created, comment))
        except (KeyError, TypeError, ValueError):
            continue
    if not fresh:
        raise Stop(2, f"issue #{issue} stays open: no QM comment created after this run started")
    verdict = max(fresh, key=lambda item: item[0])[1]["body"] or ""
    commits = re.findall(r"^Verified-Commit:[ \t]*([^\s]+)[ \t]*$", verdict, re.M)
    if not commits:
        raise Stop(2, f"issue #{issue} stays open: QM verdict has no Verified-Commit line")
    if commits != [commit]:
        raise Stop(2, f"issue #{issue} stays open: QM Verified-Commit does not match {commit}")
    if not re.search(r"^QM-Verdict:[ \t]*PASS[ \t]*$", verdict, re.M):
        raise Stop(2, f"issue #{issue} stays open: QM did not give 'QM-Verdict: PASS'")

    body = gh_json(["issue", "view", str(issue), "--json", "body"])["body"].replace("- [ ] **AC", "- [x] **AC")
    body_file = os.path.join(run_dir, "body.md")
    with open(body_file, "w") as f:
        f.write(body)
    gh(["issue", "edit", str(issue), "--body-file", body_file], role="architect")
    gh(["issue", "close", str(issue), "--reason", "completed", "--comment",
        f"Closed as completed after the QM verdict above; pull request #{number} merged under the rules on "
        "main. Acceptance criteria ticked on the basis of that verdict.\n\n"
        "Role: Architect (pipeline) · Model: none"], role="architect")
    say(f"issue #{issue} closed as completed")
    return 0


def cleanup_checkout(cwd=None):
    """Switch checkout back to main when the working tree is clean (D-047).

    When the checkout is on a change branch and the tree is not clean,
    leaves it as it is and says so in its last message, naming the branch.
    """
    branch = run(["git", "branch", "--show-current"], cwd=cwd, check=False).stdout.strip()
    if not branch or branch == "HEAD":
        branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd, check=False).stdout.strip()
        if branch == "HEAD":
            branch = run(["git", "rev-parse", "--short", "HEAD"], cwd=cwd, check=False).stdout.strip()
    if branch == "main" or not branch:
        return
    is_dirty = bool(run(["git", "status", "--porcelain"], cwd=cwd, check=False).stdout.strip())
    if is_dirty:
        say(f"the working tree is not clean; checkout left on {branch}")
    else:
        res = run(["git", "switch", "main"], cwd=cwd, check=False)
        if res.returncode != 0:
            say(f"failed to switch checkout back to main: {(res.stdout or '').strip()}")


def run_self_tests():
    """Run self-tests verifying that pinned tooling runs instead of branch tooling (D-040, AC2)."""
    global TOOLING, gh, gh_json, agent, pr_state, say, REQUIRED_CHECKS
    print("Running agent-pipeline self-tests...")
    with tempfile.TemporaryDirectory() as tmpdir:
        # Test 1: pin_tooling copies all required files and marks them executable (AC1)
        tooling_root = pin_tooling(os.path.join(tmpdir, "run1"), root=ROOT)
        assert os.path.isfile(tooling_root.agent_run), f"missing {tooling_root.agent_run}"
        assert os.path.isfile(tooling_root.agent_token), f"missing {tooling_root.agent_token}"
        assert os.path.isfile(tooling_root.role_guard), f"missing {tooling_root.role_guard}"
        assert os.path.isdir(tooling_root.agents_dir), f"missing {tooling_root.agents_dir}"
        assert os.access(tooling_root.agent_run, os.X_OK), "agent_run not executable"
        assert os.access(tooling_root.agent_token, os.X_OK), "agent_token not executable"
        assert os.access(tooling_root.role_guard, os.X_OK), "role_guard not executable"
        for r in ("architect", "developer", "reviewer", "qm", "devops"):
            assert os.path.isfile(os.path.join(tooling_root.agents_dir, f"{r}.md")), f"missing {r}.md"
        assert not tooling_root.tooling_dir.startswith(ROOT + os.sep), "tooling_dir must be outside ROOT"
        print("test_pin_tooling: OK")

        # Test 2: AC2 verification on a change branch with broken scripts (AC2)
        repo_dir = os.path.join(tmpdir, "test_repo")
        run_dir = os.path.join(tmpdir, "run2")
        os.makedirs(repo_dir)
        os.makedirs(run_dir)

        # Initialize mock repo on main
        subprocess.run(["git", "init", "-q", "-b", "main", repo_dir], check=True)
        subprocess.run(["git", "-C", repo_dir, "config", "user.name", "Pipeline Test"], check=True)
        subprocess.run(["git", "-C", repo_dir, "config", "user.email", "pipeline-test@example.com"], check=True)

        scripts_dir = os.path.join(repo_dir, "scripts")
        hooks_dir = os.path.join(repo_dir, ".claude", "hooks")
        agents_dir = os.path.join(repo_dir, ".claude", "agents")
        os.makedirs(scripts_dir)
        os.makedirs(hooks_dir)
        os.makedirs(agents_dir)

        # Main version of scripts
        main_run_sh = os.path.join(scripts_dir, "agent-run.sh")
        with open(main_run_sh, "w") as f:
            f.write("#!/bin/sh\necho \"PINNED_AGENT_RUN_SUCCESS: role=$1\"\nexit 0\n")
        os.chmod(main_run_sh, 0o755)

        main_token_sh = os.path.join(scripts_dir, "agent-token.sh")
        with open(main_token_sh, "w") as f:
            f.write("#!/bin/sh\necho \"PINNED_TOKEN_SUCCESS\"\nexit 0\n")
        os.chmod(main_token_sh, 0o755)

        main_guard_py = os.path.join(hooks_dir, "role-guard.py")
        with open(main_guard_py, "w") as f:
            f.write("#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n")
        os.chmod(main_guard_py, 0o755)

        with open(os.path.join(agents_dir, "devops.md"), "w") as f:
            f.write("---\nname: devops\n---\nDevOps test brief\n")

        subprocess.run(["git", "-C", repo_dir, "add", "."], check=True)
        subprocess.run(["git", "-C", repo_dir, "commit", "-q", "-m", "main commit"], check=True)

        # Pin tooling while on main (stored in run_dir, outside repo)
        tooling = pin_tooling(run_dir, root=repo_dir)

        # Create and switch to change branch
        subprocess.run(["git", "-C", repo_dir, "switch", "-q", "-c", "ops/test-branch"], check=True)

        # On the change branch, replace scripts/agent-run.sh with a script that exits 127
        with open(main_run_sh, "w") as f:
            f.write("#!/bin/sh\necho \"BRANCH_AGENT_RUN_FAIL\" >&2\nexit 127\n")
        os.chmod(main_run_sh, 0o755)

        # Also replace scripts/agent-token.sh with exit 127
        with open(main_token_sh, "w") as f:
            f.write("#!/bin/sh\necho \"BRANCH_TOKEN_FAIL\" >&2\nexit 127\n")
        os.chmod(main_token_sh, 0o755)

        # Replace role-guard.py with one that denies everything (writes to stdout)
        with open(main_guard_py, "w") as f:
            f.write("#!/usr/bin/env python3\nimport sys\nsys.stdout.write('REFUSED_BY_BRANCH_GUARD\\n')\nsys.exit(0)\n")
        os.chmod(main_guard_py, 0o755)

        # Confirm branch scripts fail if invoked directly
        branch_run_res = subprocess.run([main_run_sh, "devops"], capture_output=True, text=True)
        assert branch_run_res.returncode == 127, f"Branch script did not exit 127: {branch_run_res.returncode}"
        branch_token_res = subprocess.run([main_token_sh, "devops"], capture_output=True, text=True)
        assert branch_token_res.returncode == 127, f"Branch token did not exit 127: {branch_token_res.returncode}"
        print(f"test_ac2: direct branch agent-run.sh exit {branch_run_res.returncode}")

        # Test that pipeline functions use the pinned copies, NOT the branch's
        old_tooling = TOOLING
        try:
            TOOLING = tooling
            log_path = os.path.join(run_dir, "test.log")

            # agent() runs the pinned script and not the branch script (AC2)
            out = agent("devops", "test task", log=log_path, cwd=repo_dir)
            assert "PINNED_AGENT_RUN_SUCCESS: role=devops" in out, f"Pinned agent-run did not run! Output: {out}"
            assert "BRANCH_AGENT_RUN_FAIL" not in out, f"Branch agent-run ran! Output: {out}"

            # token() runs the pinned script and not the branch script
            tok = token("devops", cwd=repo_dir)
            assert tok == "PINNED_TOKEN_SUCCESS", f"Pinned token did not run! Token: {tok}"

            # outside_role_paths() runs the pinned guard (which exits 0 with empty stdout)
            refused = outside_role_paths("devops", ["scripts/agent-run.sh"], [], cwd=repo_dir)
            assert refused == [], f"Pinned guard did not run (branch guard was used): {refused}"
        finally:
            TOOLING = old_tooling

        print("test_ac2: pinned agent-run executed; branch script does not run")
        print("test_ac2_change_branch_broken_agent_run: OK")

        # Test 3: verify pinned agent-run.sh uses pinned agent definitions via stubbed agy
        stub_bin = os.path.join(tmpdir, "stub_bin")
        os.makedirs(stub_bin)
        with open(os.path.join(stub_bin, "gh"), "w") as f:
            f.write("#!/bin/sh\necho 12345\n")
        os.chmod(os.path.join(stub_bin, "gh"), 0o755)
        with open(os.path.join(stub_bin, "agy"), "w") as f:
            f.write('#!/bin/sh\necho "AGY_PROMPT: $@"\n')
        os.chmod(os.path.join(stub_bin, "agy"), 0o755)
        # Codex writes its final message to the file named after --output-last-message; the prompt is the last argument.
        with open(os.path.join(stub_bin, "codex"), "w") as f:
            f.write('#!/bin/sh\nout=\nwhile [ $# -gt 1 ]; do [ "$1" = --output-last-message ] && out=$2; shift; done\n'
                    'echo "stub progress that must not reach the caller"\necho "CODEX_PROMPT: $1" > "$out"\n')
        os.chmod(os.path.join(stub_bin, "codex"), 0o755)

        # Stub agent-token.sh in pinned tooling so agent-run.sh needs no network
        with open(tooling_root.agent_token, "w") as f:
            f.write("#!/bin/sh\necho test_token\n")

        custom_agents_dir = os.path.join(tmpdir, "custom_agents")
        os.makedirs(custom_agents_dir)
        with open(os.path.join(custom_agents_dir, "devops.md"), "w") as f:
            f.write("---\nname: devops\n---\nCUSTOM_AGENT_BRIEF_CONTENT\n")

        # 1. Fallback to pinned $here/../.claude/agents when NEXTOOMIO_AGENTS_DIR is unset
        pinned_fallback_marker = "PINNED_AGENT_DEFINITION_FALLBACK_MARKER"
        with open(os.path.join(tooling_root.agents_dir, "devops.md"), "a") as f:
            f.write(f"\n{pinned_fallback_marker}\n")

        env_fallback = dict(os.environ,
                            PATH=f"{stub_bin}:{os.environ.get('PATH', '')}",
                            NEXTOOMIO_STATE_DIR=os.path.join(tmpdir, "state"))
        env_fallback.pop("NEXTOOMIO_AGENTS_DIR", None)
        env_fallback.pop("NEXTOOMIO_ENGINE", None)
        res_fallback = subprocess.run([tooling_root.agent_run, "devops", "-p", "fallback task"],
                                      capture_output=True, text=True, env=env_fallback)
        assert res_fallback.returncode == 0, f"agent-run.sh fallback failed: {res_fallback.stderr}"
        assert pinned_fallback_marker in res_fallback.stdout, f"Pinned fallback marker not found in output: {res_fallback.stdout}"

        # 2. Use NEXTOOMIO_AGENTS_DIR when set
        env_custom = dict(env_fallback, NEXTOOMIO_AGENTS_DIR=custom_agents_dir)
        res_custom = subprocess.run([tooling_root.agent_run, "devops", "-p", "custom task"],
                                    capture_output=True, text=True, env=env_custom)
        assert res_custom.returncode == 0, f"agent-run.sh with NEXTOOMIO_AGENTS_DIR failed: {res_custom.stderr}"
        assert "CUSTOM_AGENT_BRIEF_CONTENT" in res_custom.stdout, f"Custom brief not found: {res_custom.stdout}"
        assert "custom task" in res_custom.stdout, f"Custom task not found: {res_custom.stdout}"

        assert "{{COMMIT}}" in res_custom.stdout, f"Marker instruction missing from agent prompt: {res_custom.stdout}"
        assert "'Image version and commit'" in res_custom.stdout, f"Field instruction missing from agent prompt: {res_custom.stdout}"
        print("test_agent_run_pinned_agents: OK")

        # 3. Author roles start in Codex by default and only its final message is passed on;
        #    NEXTOOMIO_ENGINE=antigravity starts Antigravity; an unknown engine is refused.
        assert "CODEX_PROMPT:" in res_custom.stdout, f"Codex was not the default author program: {res_custom.stdout}"
        assert "stub progress" not in res_custom.stdout, f"Codex progress leaked into the output: {res_custom.stdout}"
        assert "(Codex)" in res_custom.stdout, f"Model line does not name Codex: {res_custom.stdout}"
        res_agy = subprocess.run([tooling_root.agent_run, "devops", "-p", "agy task"], capture_output=True, text=True,
                                 env=dict(env_custom, NEXTOOMIO_ENGINE="antigravity"))
        assert res_agy.returncode == 0 and "AGY_PROMPT:" in res_agy.stdout, f"Antigravity not started: {res_agy.stdout} {res_agy.stderr}"
        assert "(Antigravity)" in res_agy.stdout, f"Model line does not name Antigravity: {res_agy.stdout}"
        res_bad = subprocess.run([tooling_root.agent_run, "devops", "-p", "x"], capture_output=True, text=True,
                                 env=dict(env_custom, NEXTOOMIO_ENGINE="other"))
        assert res_bad.returncode == 2, f"Unknown engine was not refused: {res_bad.returncode}"
        # 4. The role-and-model line follows the engine (AC3).
        assert author_model({}) == "gpt-6.1-terra (Codex)"
        assert author_model({"NEXTOOMIO_ENGINE": "codex", "NEXTOOMIO_CODEX_MODEL": "m1"}) == "m1 (Codex)"
        assert author_model({"NEXTOOMIO_ENGINE": "antigravity"}) == "gemini-3.8-flash-high (Antigravity)"
        assert author_model({"NEXTOOMIO_ENGINE": "claude"}) == "opus (Claude Code)"
        assert "Codex" not in author_model({"NEXTOOMIO_ENGINE": "other"})
        assert build_pr_body("devops", 1, "S").endswith(f"Role: DevOps · Model: {AUTHOR_MODEL}")
        print("test_author_engine_selection: OK")

        # Test 4: AC1 verification of replace_commit_markers and update_evidence_commit_markers (D-039)
        test_text = (
            "# Evidence: P0-F9.9 Test\n"
            "- **Date:** 2026-11-14\n"
            "- **Image version and commit:** none (repository only); commit {{COMMIT}}\n"
            "- **Hardware model and revision:** none\n"
            "- **Physical access needed:** none\n\n"
            "## Procedure\n"
            "Verify that {{COMMIT}} in prose is preserved and not replaced.\n\n"
            "## Observed result\n"
            "The {{COMMIT}} marker in prose remains.\n\n"
            "## Missed targets\nNone.\n"
        )
        fake_sha = "0123456789abcdef0123456789abcdef01234567"
        replaced_text, rep_count = replace_commit_marker_in_text(test_text, fake_sha)
        assert rep_count == 1, f"Expected exactly 1 replacement, got {rep_count}"
        assert f"commit {fake_sha}" in replaced_text, f"Field not updated: {replaced_text}"
        assert "Verify that {{COMMIT}} in prose is preserved" in replaced_text, "Prose marker in procedure was rewritten!"
        assert "The {{COMMIT}} marker in prose remains" in replaced_text, "Prose marker in observed result rewritten!"

        # In-repo git workflow test for update_evidence_commit_markers
        d039_repo = os.path.join(tmpdir, "d039_repo")
        os.makedirs(d039_repo)
        subprocess.run(["git", "init", "-q", "-b", "main", d039_repo], check=True)
        subprocess.run(["git", "-C", d039_repo, "config", "user.name", "Pipeline Test"], check=True)
        subprocess.run(["git", "-C", d039_repo, "config", "user.email", "pipeline-test@example.com"], check=True)

        readme_d039 = os.path.join(d039_repo, "README.md")
        with open(readme_d039, "w") as f:
            f.write("# Test\n")
        subprocess.run(["git", "-C", d039_repo, "add", "README.md"], check=True)
        subprocess.run(["git", "-C", d039_repo, "commit", "-q", "-m", "Initial commit"], check=True)

        subprocess.run(["git", "-C", d039_repo, "switch", "-q", "-c", "ops/P0-F9.9-test"], check=True)

        code_file = os.path.join(d039_repo, "feature.py")
        with open(code_file, "w") as f:
            f.write("def run():\n    return 42\n")

        ev_dir = os.path.join(d039_repo, "docs", "evidence", "p0")
        os.makedirs(ev_dir)
        ev_file = os.path.join(ev_dir, "P0-F9.9.md")
        ev_rel = "docs/evidence/p0/P0-F9.9.md"
        with open(ev_file, "w") as f:
            f.write(test_text)

        paths = ["feature.py", ev_rel]
        subprocess.run(["git", "-C", d039_repo, "add", "-A"], check=True)
        subprocess.run(["git", "-C", d039_repo, "commit", "-q", "-m", "Implement feature and add evidence\n\nRefs #99"], check=True)

        change_commit = subprocess.run(["git", "-C", d039_repo, "rev-parse", "HEAD"],
                                       capture_output=True, text=True, check=True).stdout.strip()
        assert len(change_commit) == 40, f"Expected 40-character sha, got: {change_commit}"

        # Verify change commit contains the feature code
        show_code = subprocess.run(["git", "-C", d039_repo, "show", f"{change_commit}:feature.py"],
                                   capture_output=True, text=True, check=True).stdout
        assert "def run():" in show_code

        # Run update_evidence_commit_markers (the exact function deliver() calls)
        updated = update_evidence_commit_markers(paths, change_commit, 99, root=d039_repo)
        assert updated == [ev_rel], f"Expected [{ev_rel}], got: {updated}"

        follow_up_commit = subprocess.run(["git", "-C", d039_repo, "rev-parse", "HEAD"],
                                          capture_output=True, text=True, check=True).stdout.strip()
        assert follow_up_commit != change_commit

        # Verify follow-up commit message and touched files
        commit_msg = subprocess.run(["git", "-C", d039_repo, "log", "-1", "--format=%B", follow_up_commit],
                                    capture_output=True, text=True, check=True).stdout.strip()
        assert commit_msg == "Record change commit in evidence\n\nRefs #99"

        diff_files = subprocess.run(["git", "-C", d039_repo, "diff-tree", "--no-commit-id", "--name-only", "-r", follow_up_commit],
                                    capture_output=True, text=True, check=True).stdout.strip().splitlines()
        assert diff_files == [ev_rel], f"Follow-up commit changed unexpected files: {diff_files}"

        # Verify file content at follow_up_commit
        ev_after = subprocess.run(["git", "-C", d039_repo, "show", f"{follow_up_commit}:{ev_rel}"],
                                  capture_output=True, text=True, check=True).stdout
        assert f"- **Image version and commit:** none (repository only); commit {change_commit}" in ev_after
        assert "Verify that {{COMMIT}} in prose is preserved" in ev_after
        assert "The {{COMMIT}} marker in prose remains" in ev_after

        print("test_d039_commit_marker_replacement: OK")

        # Test 5: AC1 verification of choose_branch with no open PR, stale branch, and pruned ref (D-042)
        d042_origin = os.path.join(tmpdir, "d042_origin")
        d042_local = os.path.join(tmpdir, "d042_local")
        os.makedirs(d042_origin)
        subprocess.run(["git", "init", "-q", "-b", "main", d042_origin], check=True)
        subprocess.run(["git", "-C", d042_origin, "config", "user.name", "Pipeline Test"], check=True)
        subprocess.run(["git", "-C", d042_origin, "config", "user.email", "pipeline-test@example.com"], check=True)
        subprocess.run(["git", "-C", d042_origin, "commit", "-q", "--allow-empty", "-m", "Initial main commit"], check=True)
        c_main1 = subprocess.run(["git", "-C", d042_origin, "rev-parse", "HEAD"],
                                 capture_output=True, text=True, check=True).stdout.strip()

        # Create branch on origin with a commit
        subprocess.run(["git", "-C", d042_origin, "switch", "-q", "-c", "ops/D-042-stale"], check=True)
        subprocess.run(["git", "-C", d042_origin, "commit", "-q", "--allow-empty", "-m", "Stale branch commit"], check=True)
        c_stale = subprocess.run(["git", "-C", d042_origin, "rev-parse", "HEAD"],
                                 capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "-C", d042_origin, "switch", "-q", "main"], check=True)

        # Clone origin into local
        subprocess.run(["git", "clone", "-q", d042_origin, d042_local], check=True)
        subprocess.run(["git", "-C", d042_local, "config", "user.name", "Pipeline Test"], check=True)
        subprocess.run(["git", "-C", d042_local, "config", "user.email", "pipeline-test@example.com"], check=True)

        # In local, checkout the branch so local branch exists tracking origin/ops/D-042-stale
        subprocess.run(["git", "-C", d042_local, "switch", "-q", "-c", "ops/D-042-stale", "origin/ops/D-042-stale"], check=True)
        subprocess.run(["git", "-C", d042_local, "switch", "-q", "main"], check=True)

        # Advance main on origin and delete the remote branch
        subprocess.run(["git", "-C", d042_origin, "commit", "-q", "--allow-empty", "-m", "Second main commit"], check=True)
        c_main2 = subprocess.run(["git", "-C", d042_origin, "rev-parse", "HEAD"],
                                 capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "-C", d042_origin, "branch", "-D", "ops/D-042-stale"], capture_output=True, check=True)

        # Pre-conditions check for AC1:
        # 1. Local branch exists and points to c_stale
        assert subprocess.run(["git", "-C", d042_local, "rev-parse", "ops/D-042-stale"],
                              capture_output=True, text=True, check=True).stdout.strip() == c_stale
        # 2. Stale remote-tracking ref still exists in local before fetch --prune
        assert subprocess.run(["git", "-C", d042_local, "rev-parse", "--verify", "-q", "origin/ops/D-042-stale"],
                              capture_output=True, text=True).returncode == 0

        # Execute choose_branch with no open PR
        chosen_ac1 = choose_branch("ops/D-042-stale", pr=None, cwd=d042_local)
        assert chosen_ac1 == "ops/D-042-stale"

        # Post-conditions check for AC1:
        # 1. Author starts on the branch
        curr_branch = subprocess.run(["git", "-C", d042_local, "branch", "--show-current"],
                                     capture_output=True, text=True, check=True).stdout.strip()
        assert curr_branch == "ops/D-042-stale"
        # 2. Branch commit is the remote's current main (c_main2)
        curr_head = subprocess.run(["git", "-C", d042_local, "rev-parse", "HEAD"],
                                   capture_output=True, text=True, check=True).stdout.strip()
        assert curr_head == c_main2, f"Expected {c_main2}, got {curr_head}"
        assert curr_head != c_stale
        # 3. Stale remote-tracking ref was pruned
        assert subprocess.run(["git", "-C", d042_local, "rev-parse", "--verify", "-q", "origin/ops/D-042-stale"],
                              capture_output=True, text=True).returncode != 0
        print("test_d042_ac1_stale_branch_reset_to_origin_main: OK")

        # Test 6: AC2 verification of choose_branch with open PR ahead of local branch (D-042)
        subprocess.run(["git", "-C", d042_local, "switch", "-q", "main"], check=True)

        # Create PR branch on origin at c_main2
        subprocess.run(["git", "-C", d042_origin, "switch", "-q", "-c", "ops/D-042-pr"], check=True)
        subprocess.run(["git", "-C", d042_origin, "commit", "-q", "--allow-empty", "-m", "PR commit 1"], check=True)
        c_pr1 = subprocess.run(["git", "-C", d042_origin, "rev-parse", "HEAD"],
                               capture_output=True, text=True, check=True).stdout.strip()

        # In local, track the branch at c_pr1
        subprocess.run(["git", "-C", d042_local, "fetch", "-q", "origin"], check=True)
        subprocess.run(["git", "-C", d042_local, "switch", "-q", "-c", "ops/D-042-pr", "origin/ops/D-042-pr"], check=True)
        subprocess.run(["git", "-C", d042_local, "switch", "-q", "main"], check=True)

        # Advance PR branch on origin
        subprocess.run(["git", "-C", d042_origin, "commit", "-q", "--allow-empty", "-m", "PR commit 2 (ahead)"], check=True)
        c_pr2 = subprocess.run(["git", "-C", d042_origin, "rev-parse", "HEAD"],
                               capture_output=True, text=True, check=True).stdout.strip()
        assert c_pr1 != c_pr2

        # Pre-condition check for AC2: local branch is behind remote
        assert subprocess.run(["git", "-C", d042_local, "rev-parse", "ops/D-042-pr"],
                              capture_output=True, text=True, check=True).stdout.strip() == c_pr1

        # Execute choose_branch with open PR (also verifying headRefName override if branch differs)
        mock_pr = {"number": 173, "headRefName": "ops/D-042-pr"}
        chosen_ac2 = choose_branch("ops/D-042-fallback", pr=mock_pr, cwd=d042_local)
        assert chosen_ac2 == "ops/D-042-pr"

        # Post-conditions check for AC2:
        # 1. Local branch is switched to ops/D-042-pr
        curr_branch_pr = subprocess.run(["git", "-C", d042_local, "branch", "--show-current"],
                                        capture_output=True, text=True, check=True).stdout.strip()
        assert curr_branch_pr == "ops/D-042-pr"
        # 2. Local branch commit is at remote branch commit (c_pr2)
        curr_head_pr = subprocess.run(["git", "-C", d042_local, "rev-parse", "HEAD"],
                                      capture_output=True, text=True, check=True).stdout.strip()
        assert curr_head_pr == c_pr2, f"Expected {c_pr2}, got {curr_head_pr}"
        print("test_d042_ac2_open_pr_updates_local_to_remote_commit: OK")

        # Test 7: AC1 and AC2 verification of role session failure handling (D-044)
        failing_run_sh = os.path.join(tmpdir, "failing-agent-run.sh")
        with open(failing_run_sh, "w") as f:
            f.write("#!/bin/sh\necho \"Error: session failed with usage limit or token error\" >&2\nexit 1\n")
        os.chmod(failing_run_sh, 0o755)

        d044_log = os.path.join(tmpdir, "agents-d044.log")
        fail_tooling = Tooling(
            run_dir=tmpdir,
            tooling_dir=tmpdir,
            agent_run=failing_run_sh,
            agent_token=tooling_root.agent_token,
            role_guard=tooling_root.role_guard,
            agents_dir=tooling_root.agents_dir,
        )

        old_tooling = TOOLING
        try:
            TOOLING = fail_tooling

            # AC1: agent() raises Stop(1) naming the role, exit status, and run log
            role_failed = False
            try:
                agent("reviewer", "Review pull request #168", log=d044_log)
            except Stop as exc:
                role_failed = True
                assert exc.status == 1, f"Expected Stop status 1, got {exc.status}"
                assert "reviewer" in str(exc), f"Role name not in error message: {exc}"
                assert "1" in str(exc), f"Status not in error message: {exc}"
                assert d044_log in str(exc), f"Log path not in error message: {exc}"
            assert role_failed, "agent() did not raise Stop on non-zero exit status"

            # Check that log file recorded the failure output
            with open(d044_log, "r") as f:
                log_content = f.read()
            assert "Error: session failed with usage limit or token error" in log_content
            print("test_d044_ac1_session_exit_status_stops_with_exit_1: OK")

            # AC1 check with another exit code (e.g. 127) and developer role
            with open(failing_run_sh, "w") as f:
                f.write("#!/bin/sh\necho \"Command not found: agy\" >&2\nexit 127\n")
            dev_failed = False
            try:
                agent("developer", "Implement task #178", log=d044_log)
            except Stop as exc:
                dev_failed = True
                assert exc.status == 1, f"Expected Stop status 1, got {exc.status}"
                assert "developer" in str(exc), f"Role name not in error message: {exc}"
                assert "127" in str(exc), f"Status 127 not in error message: {exc}"
                assert d044_log in str(exc), f"Log path not in error message: {exc}"
            assert dev_failed, "agent() did not raise Stop for developer on exit 127"
            print("test_d044_ac1_session_exit_127_stops_with_exit_1: OK")

            # AC2: Given the failure, verify no calls modify issue labels or comment on issue
            gh_issue_mutations = []
            def tracking_gh(args, role=None, check=True):
                # Detect any command that edits, comments on, or closes an issue
                if len(args) >= 2 and args[0] == "issue" and args[1] in ("edit", "comment", "close"):
                    gh_issue_mutations.append(args)
                return "{}"

            # Monkeypatch gh during pipeline caller simulation
            real_gh = gh
            gh = tracking_gh
            try:
                # Reviewer failure scenario (D-044 / #168):
                # Pipeline starts reviewer; on failure, execution must abort without calling block()
                # or modifying the issue.
                reviewer_aborted = False
                try:
                    agent("reviewer", "Review pull request #168", log=d044_log)
                    verdict, head = None, "head123"
                    if verdict is None:
                        block(168, f"the Reviewer submitted no verdict on pull request #42 at {head}.")
                except Stop as stop_exc:
                    reviewer_aborted = True
                    assert stop_exc.status == 1

                assert reviewer_aborted, "Reviewer failure did not abort pipeline execution"
                assert gh_issue_mutations == [], f"AC2 violated: issue mutation calls made on reviewer failure: {gh_issue_mutations}"

                # Author failure scenario:
                author_aborted = False
                try:
                    output = agent("devops", "Implement task", log=d044_log)
                    delivered = deliver("devops", 178, "Title", "ops/test", output, [], None)
                    if not delivered:
                        block(178, "the devops finished without changing anything.")
                except Stop as stop_exc:
                    author_aborted = True
                    assert stop_exc.status == 1

                assert author_aborted, "Author failure did not abort pipeline execution"
                assert gh_issue_mutations == [], f"AC2 violated: issue mutation calls made on author failure: {gh_issue_mutations}"

                # QM failure scenario:
                qm_aborted = False
                try:
                    agent("qm", "Verify task", log=d044_log)
                except Stop as stop_exc:
                    qm_aborted = True
                    assert stop_exc.status == 1

                assert qm_aborted, "QM failure did not abort pipeline execution"
                assert gh_issue_mutations == [], f"AC2 violated: issue mutation calls made on QM failure: {gh_issue_mutations}"

                # Contrast check: verify that block() DOES call gh to edit and comment
                try:
                    block(999, "test block reason")
                except Stop:
                    pass
                assert len(gh_issue_mutations) == 2, f"Expected 2 issue mutations from block(), got {len(gh_issue_mutations)}"
                assert gh_issue_mutations[0][:4] == ["issue", "edit", "999", "--remove-label"]
                assert gh_issue_mutations[1][:3] == ["issue", "comment", "999"]
            finally:
                gh = real_gh

            print("test_d044_ac2_no_issue_label_changes_or_comments_on_failure: OK")

            # Test 8: AC1, AC2, and AC3 verification of conflicting pull request handling (D-045)
            real_pr_state = pr_state
            real_gh = gh
            real_sleep = time.sleep
            real_time = time.time
            old_required_checks_d045 = REQUIRED_CHECKS
            REQUIRED_CHECKS = {
                "Traceability, Docs & Yocto Layer Check",
                "Rust Core & License Gate",
                "WebUI & i18n Catalog Gate",
            }

            try:
                # AC1: Conflicting PR with no checks stops on first poll and blocks issue naming conflict and PR
                gh_mutations = []
                def tracking_gh_d045(args, role=None, check=True):
                    if len(args) >= 2 and args[0] == "issue" and args[1] in ("edit", "comment", "close"):
                        gh_mutations.append(args)
                    return "{}"
                gh = tracking_gh_d045

                # 1. PR with mergeStateStatus: DIRTY and mergeable: CONFLICTING, no checks
                poll_count_ac1 = 0
                sleep_calls_ac1 = []
                time.sleep = lambda s: sleep_calls_ac1.append(s)

                def mock_pr_state_dirty(pr_num):
                    nonlocal poll_count_ac1
                    poll_count_ac1 += 1
                    return {
                        "headRefOid": "668123af",
                        "mergeStateStatus": "DIRTY",
                        "mergeable": "CONFLICTING",
                        "statusCheckRollup": [],
                        "body": "Refs #179\n\nConflicting PR",
                    }
                pr_state = mock_pr_state_dirty

                ac1_blocked = False
                try:
                    wait_for_ci(172, issue=179)
                except Stop as exc:
                    ac1_blocked = True
                    assert exc.status == 2, f"Expected Stop status 2, got {exc.status}"
                    assert "issue #179 blocked" in str(exc), f"Issue number missing from Stop error: {exc}"
                    assert "pull request #172 conflicts with main" in str(exc), f"Conflict or PR missing from Stop error: {exc}"

                assert ac1_blocked, "wait_for_ci did not raise Stop on conflicting PR"
                assert poll_count_ac1 == 1, f"Expected exactly 1 poll on conflict, got {poll_count_ac1}"
                assert sleep_calls_ac1 == [], f"Expected 0 sleep calls on immediate conflict detection, got {sleep_calls_ac1}"
                assert len(gh_mutations) == 2, f"Expected 2 issue mutations (edit and comment), got {len(gh_mutations)}"
                assert gh_mutations[0][:4] == ["issue", "edit", "179", "--remove-label"]
                assert "--add-label" in gh_mutations[0] and "state:blocked" in gh_mutations[0]
                assert gh_mutations[1][:3] == ["issue", "comment", "179"]
                assert "pull request #172 conflicts with main" in gh_mutations[1][4]
                print("test_d045_ac1_conflicting_pr_blocks_on_first_poll: OK")

                # 2. PR with inferred issue from body when issue parameter is None
                gh_mutations.clear()
                poll_count_inferred = 0
                def mock_pr_state_inferred(pr_num):
                    nonlocal poll_count_inferred
                    poll_count_inferred += 1
                    return {
                        "headRefOid": "668123af",
                        "mergeStateStatus": "DIRTY",
                        "mergeable": "CONFLICTING",
                        "statusCheckRollup": [],
                        "body": "Refs #168\n\nPR body with issue ref",
                    }
                pr_state = mock_pr_state_inferred

                inferred_blocked = False
                try:
                    wait_for_ci(172)
                except Stop as exc:
                    inferred_blocked = True
                    assert exc.status == 2
                    assert "issue #168 blocked" in str(exc)
                    assert "pull request #172 conflicts with main" in str(exc)
                assert inferred_blocked, "wait_for_ci did not infer issue and block"
                assert poll_count_inferred == 1
                assert len(gh_mutations) == 2
                assert gh_mutations[0][2] == "168"
                assert gh_mutations[1][2] == "168"
                print("test_d045_ac1_inferred_issue_from_pr_body: OK")

                # 3. Test conflict detection with mergeStateStatus == DIRTY alone or mergeable == CONFLICTING alone
                gh_mutations.clear()
                pr_state = lambda pr_num: {
                    "mergeStateStatus": "DIRTY",
                    "mergeable": "UNKNOWN",
                    "statusCheckRollup": [],
                    "body": "Refs #179",
                }
                try:
                    wait_for_ci(172, issue=179)
                    assert False, "Expected Stop on DIRTY alone"
                except Stop as exc:
                    assert exc.status == 2
                    assert "pull request #172 conflicts with main" in str(exc)

                gh_mutations.clear()
                pr_state = lambda pr_num: {
                    "mergeStateStatus": "BLOCKED",
                    "mergeable": "CONFLICTING",
                    "statusCheckRollup": [],
                    "body": "Refs #179",
                }
                try:
                    wait_for_ci(172, issue=179)
                    assert False, "Expected Stop on CONFLICTING alone"
                except Stop as exc:
                    assert exc.status == 2
                    assert "pull request #172 conflicts with main" in str(exc)
                print("test_d045_ac1_conflict_detection_variants: OK")

                # AC2: Non-conflicting PR with pending checks keeps waiting as before
                gh_mutations.clear()
                sleep_calls_ac2 = []
                time.sleep = lambda s: sleep_calls_ac2.append(s)

                # Scenario 2a: Checks not reported yet initially, then report SUCCESS on 3rd poll
                poll_count_ac2 = 0
                def mock_pr_state_pending_then_success(pr_num):
                    nonlocal poll_count_ac2
                    poll_count_ac2 += 1
                    if poll_count_ac2 < 3:
                        return {
                            "headRefOid": "abc1234",
                            "mergeStateStatus": "BLOCKED",
                            "mergeable": "MERGEABLE",
                            "statusCheckRollup": [],
                            "body": "Refs #179",
                        }
                    return {
                        "headRefOid": "abc1234",
                        "mergeStateStatus": "CLEAN",
                        "mergeable": "MERGEABLE",
                        "statusCheckRollup": [{"name": name, "conclusion": "SUCCESS"} for name in REQUIRED_CHECKS],
                        "body": "Refs #179",
                    }
                pr_state = mock_pr_state_pending_then_success

                res = wait_for_ci(173, issue=179)
                assert res is True, "wait_for_ci did not return True after pending checks passed"
                assert poll_count_ac2 == 3, f"Expected 3 polls, got {poll_count_ac2}"
                assert sleep_calls_ac2 == [20, 20], f"Expected 2 sleep calls of 20s, got {sleep_calls_ac2}"
                assert gh_mutations == [], f"AC2 violated: issue mutations occurred during normal wait: {gh_mutations}"
                print("test_d045_ac2_pending_checks_keeps_waiting_then_succeeds: OK")

                # Scenario 2b: Checks not reported yet and timeout reached raises Stop(1) without blocking issue
                gh_mutations.clear()
                sleep_calls_timeout = []
                simulated_clock = 1000.0
                def mock_advancing_sleep(s):
                    nonlocal simulated_clock
                    sleep_calls_timeout.append(s)
                    simulated_clock += s
                time.sleep = mock_advancing_sleep
                time.time = lambda: simulated_clock

                pr_state = lambda pr_num: {
                    "headRefOid": "abc1234",
                    "mergeStateStatus": "BLOCKED",
                    "mergeable": "MERGEABLE",
                    "statusCheckRollup": [],
                    "body": "Refs #179",
                }

                timeout_raised = False
                try:
                    wait_for_ci(173, issue=179, timeout=60)
                except Stop as exc:
                    timeout_raised = True
                    assert exc.status == 1, f"Expected Stop status 1 on timeout, got {exc.status}"
                    assert "the checks on pull request #173 did not finish within 1 minutes" in str(exc)
                assert timeout_raised, "wait_for_ci did not time out as before"
                assert len(sleep_calls_timeout) > 0, "Expected sleep calls before timeout"
                assert gh_mutations == [], f"AC2 violated: issue mutations occurred on check timeout: {gh_mutations}"
                print("test_d045_ac2_timeout_raises_stop1_without_blocking: OK")

            finally:
                REQUIRED_CHECKS = old_required_checks_d045
                pr_state = real_pr_state
                gh = real_gh
                time.sleep = real_sleep
                time.time = real_time
        finally:
            TOOLING = old_tooling

        # Test 9: AC1 and AC2 verification of choose_branch with existing remote branch (D-046)
        d046_origin = os.path.join(tmpdir, "d046_origin")
        d046_local = os.path.join(tmpdir, "d046_local")
        os.makedirs(d046_origin)
        subprocess.run(["git", "init", "-q", "-b", "main", d046_origin], check=True)
        subprocess.run(["git", "-C", d046_origin, "config", "user.name", "Pipeline Test"], check=True)
        subprocess.run(["git", "-C", d046_origin, "config", "user.email", "pipeline-test@example.com"], check=True)
        subprocess.run(["git", "-C", d046_origin, "commit", "-q", "--allow-empty", "-m", "Initial main commit"], check=True)
        c_main = subprocess.run(["git", "-C", d046_origin, "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()

        # Create remote branch on origin with an unmerged/stale commit
        subprocess.run(["git", "-C", d046_origin, "switch", "-q", "-c", "ops/D-046-test"], check=True)
        subprocess.run(["git", "-C", d046_origin, "commit", "-q", "--allow-empty", "-m", "Remote branch commit 1"], check=True)
        c_remote1 = subprocess.run(["git", "-C", d046_origin, "rev-parse", "HEAD"],
                                   capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "-C", d046_origin, "switch", "-q", "main"], check=True)

        # Clone origin into local
        subprocess.run(["git", "clone", "-q", d046_origin, d046_local], check=True)
        subprocess.run(["git", "-C", d046_local, "config", "user.name", "Pipeline Test"], check=True)
        subprocess.run(["git", "-C", d046_local, "config", "user.email", "pipeline-test@example.com"], check=True)

        # Pre-conditions check for AC1:
        assert c_remote1 != c_main
        assert subprocess.run(["git", "-C", d046_origin, "rev-parse", "ops/D-046-test"],
                              capture_output=True, text=True, check=True).stdout.strip() == c_remote1
        assert subprocess.run(["git", "-C", d046_local, "rev-parse", "--verify", "-q", "origin/ops/D-046-test"],
                              capture_output=True, text=True).returncode == 0
        assert subprocess.run(["git", "-C", d046_local, "rev-parse", "--verify", "-q", "origin/ops/D-046-test-2"],
                              capture_output=True, text=True).returncode != 0

        # Execute choose_branch with no open PR (AC1)
        chosen_ac1 = choose_branch("ops/D-046-test", pr=None, cwd=d046_local)
        assert chosen_ac1 == "ops/D-046-test-2"

        # Post-conditions check for AC1:
        # 1. Author starts on <branch>-2
        curr_branch_ac1 = subprocess.run(["git", "-C", d046_local, "branch", "--show-current"],
                                         capture_output=True, text=True, check=True).stdout.strip()
        assert curr_branch_ac1 == "ops/D-046-test-2"
        # 2. Local branch commit is at remote current main (c_main)
        curr_head_ac1 = subprocess.run(["git", "-C", d046_local, "rev-parse", "HEAD"],
                                       capture_output=True, text=True, check=True).stdout.strip()
        assert curr_head_ac1 == c_main, f"Expected {c_main}, got {curr_head_ac1}"
        # 3. Existing remote branch on origin is unchanged
        assert subprocess.run(["git", "-C", d046_origin, "rev-parse", "ops/D-046-test"],
                              capture_output=True, text=True, check=True).stdout.strip() == c_remote1
        print("test_d046_ac1_existing_remote_branch_uses_suffix_2: OK")

        # Now setup for AC2: <branch>-2 also exists on remote
        subprocess.run(["git", "-C", d046_origin, "switch", "-q", "-c", "ops/D-046-test-2"], check=True)
        subprocess.run(["git", "-C", d046_origin, "commit", "-q", "--allow-empty", "-m", "Remote branch commit 2"], check=True)
        c_remote2 = subprocess.run(["git", "-C", d046_origin, "rev-parse", "HEAD"],
                                   capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "-C", d046_origin, "switch", "-q", "main"], check=True)

        # Pre-conditions check for AC2:
        assert c_remote2 != c_main and c_remote2 != c_remote1
        assert subprocess.run(["git", "-C", d046_origin, "rev-parse", "ops/D-046-test"],
                              capture_output=True, text=True, check=True).stdout.strip() == c_remote1
        assert subprocess.run(["git", "-C", d046_origin, "rev-parse", "ops/D-046-test-2"],
                              capture_output=True, text=True, check=True).stdout.strip() == c_remote2

        # Switch local back to main before choosing branch
        subprocess.run(["git", "-C", d046_local, "switch", "-q", "main"], check=True)

        # Execute choose_branch with no open PR (AC2)
        chosen_ac2 = choose_branch("ops/D-046-test", pr=None, cwd=d046_local)
        assert chosen_ac2 == "ops/D-046-test-3"

        # Post-conditions check for AC2:
        # 1. Author starts on <branch>-3
        curr_branch_ac2 = subprocess.run(["git", "-C", d046_local, "branch", "--show-current"],
                                         capture_output=True, text=True, check=True).stdout.strip()
        assert curr_branch_ac2 == "ops/D-046-test-3"
        # 2. Local branch commit is at remote current main (c_main)
        curr_head_ac2 = subprocess.run(["git", "-C", d046_local, "rev-parse", "HEAD"],
                                       capture_output=True, text=True, check=True).stdout.strip()
        assert curr_head_ac2 == c_main, f"Expected {c_main}, got {curr_head_ac2}"
        # 3. Existing remote branches on origin are unchanged
        assert subprocess.run(["git", "-C", d046_origin, "rev-parse", "ops/D-046-test"],
                              capture_output=True, text=True, check=True).stdout.strip() == c_remote1
        assert subprocess.run(["git", "-C", d046_origin, "rev-parse", "ops/D-046-test-2"],
                              capture_output=True, text=True, check=True).stdout.strip() == c_remote2
        print("test_d046_ac2_existing_remote_branches_uses_suffix_3: OK")

        # Test 10: AC1 and AC2 verification of checkout restoration to main on run completion or stop (D-047)
        d047_origin = os.path.join(tmpdir, "d047_origin")
        d047_local = os.path.join(tmpdir, "d047_local")
        os.makedirs(d047_origin)
        subprocess.run(["git", "init", "-q", "-b", "main", d047_origin], check=True)
        subprocess.run(["git", "-C", d047_origin, "config", "user.name", "Pipeline Test"], check=True)
        subprocess.run(["git", "-C", d047_origin, "config", "user.email", "pipeline-test@example.com"], check=True)
        subprocess.run(["git", "-C", d047_origin, "commit", "-q", "--allow-empty", "-m", "Initial main commit"], check=True)

        subprocess.run(["git", "clone", "-q", d047_origin, d047_local], check=True)
        subprocess.run(["git", "-C", d047_local, "config", "user.name", "Pipeline Test"], check=True)
        subprocess.run(["git", "-C", d047_local, "config", "user.email", "pipeline-test@example.com"], check=True)

        # 1. Failing author agent script (exits 1) without modifying working tree
        d047_fail_agent = os.path.join(tmpdir, "d047_agent_fail.sh")
        with open(d047_fail_agent, "w") as f:
            f.write("#!/bin/sh\necho 'Simulated author failure' >&2\nexit 1\n")
        os.chmod(d047_fail_agent, 0o755)

        d047_tooling_clean = Tooling(
            run_dir=tmpdir,
            tooling_dir=tmpdir,
            agent_run=d047_fail_agent,
            agent_token=tooling_root.agent_token,
            role_guard=tooling_root.role_guard,
            agents_dir=tooling_root.agents_dir,
        )

        def mock_gh_d047(args, role=None, check=True):
            if len(args) >= 2 and args[0] == "api" and "rules/branches/main" in args[1]:
                return json.dumps([{
                    "type": "required_status_checks",
                    "parameters": {"required_status_checks": [{"context": "test-ci-check"}]}
                }])
            if len(args) >= 2 and args[0] == "issue" and args[1] == "view":
                return json.dumps({
                    "title": "D-047 Pipeline test",
                    "state": "OPEN",
                    "labels": [{"name": "state:ready"}, {"name": "area:tooling"}],
                    "body": "### Touches\n- `scripts/agent-pipeline.py`\n",
                })
            if len(args) >= 2 and args[0] == "pr" and args[1] == "list":
                return "[]"
            return "{}"

        d047_messages = []
        real_say_d047 = say
        def recording_say_d047(msg):
            d047_messages.append(msg)
            real_say_d047(msg)

        old_tooling_d047 = TOOLING
        real_gh_d047 = gh
        try:
            TOOLING = d047_tooling_clean
            gh = mock_gh_d047
            say = recording_say_d047

            # --- AC1 Scenario: Run stops on change branch with clean working tree ---
            d047_messages.clear()
            exit_code_ac1 = main(["188", "--role", "devops"], cwd=d047_local,
                                 run_dir=os.path.join(tmpdir, "d047_run1"))

            # AC1 Post-conditions:
            # - Exit status is that of the stop (1)
            assert exit_code_ac1 == 1, f"Expected exit status 1, got {exit_code_ac1}"
            # - Checkout is restored to main
            curr_branch_ac1 = subprocess.run(["git", "-C", d047_local, "branch", "--show-current"],
                                             capture_output=True, text=True, check=True).stdout.strip()
            assert curr_branch_ac1 == "main", f"Expected checkout on main, got {curr_branch_ac1}"
            # - Working tree is clean
            clean_status = subprocess.run(["git", "-C", d047_local, "status", "--porcelain"],
                                          capture_output=True, text=True, check=True).stdout.strip()
            assert clean_status == "", f"Expected clean working tree, got {clean_status}"
            print("test_d047_ac1_clean_tree_checkout_ends_on_main: OK")

            # --- AC2 Scenario: Run stops on change branch with uncommitted changes ---
            # Author agent leaves uncommitted file and exits 1
            d047_dirty_agent = os.path.join(tmpdir, "d047_agent_dirty.sh")
            dirty_file = os.path.join(d047_local, "uncommitted_work.txt")
            with open(d047_dirty_agent, "w") as f:
                f.write(f"#!/bin/sh\necho 'uncommitted work in progress' > '{dirty_file}'\nexit 1\n")
            os.chmod(d047_dirty_agent, 0o755)

            d047_tooling_dirty = Tooling(
                run_dir=tmpdir,
                tooling_dir=tmpdir,
                agent_run=d047_dirty_agent,
                agent_token=tooling_root.agent_token,
                role_guard=tooling_root.role_guard,
                agents_dir=tooling_root.agents_dir,
            )
            TOOLING = d047_tooling_dirty
            d047_messages.clear()

            exit_code_ac2 = main(["188", "--role", "devops"], cwd=d047_local,
                                 run_dir=os.path.join(tmpdir, "d047_run2"))

            # AC2 Post-conditions:
            # - Exit status is that of the stop (1)
            assert exit_code_ac2 == 1, f"Expected exit status 1, got {exit_code_ac2}"
            # - Checkout stays on the change branch
            curr_branch_ac2 = subprocess.run(["git", "-C", d047_local, "branch", "--show-current"],
                                             capture_output=True, text=True, check=True).stdout.strip()
            assert curr_branch_ac2 == "ops/D-047-pipeline-test", f"Expected checkout on ops/D-047-pipeline-test, got {curr_branch_ac2}"
            # - Changes are untouched
            assert os.path.isfile(dirty_file), f"Uncommitted file was deleted: {dirty_file}"
            with open(dirty_file, "r") as f:
                content = f.read()
            assert content == "uncommitted work in progress\n", f"Unexpected content: {content}"
            # - Last message names the branch
            assert len(d047_messages) > 0, "No messages logged"
            last_msg = d047_messages[-1]
            assert "ops/D-047-pipeline-test" in last_msg, f"Branch not in last message: '{last_msg}'"
            assert "the working tree is not clean" in last_msg, f"Dirty status not in last message: '{last_msg}'"
            print("test_d047_ac2_uncommitted_changes_stays_on_branch_names_branch: OK")
        finally:
            TOOLING = old_tooling_d047
            gh = real_gh_d047
            say = real_say_d047

        # Test 11: AC1, AC2, and AC4 verification of required status checks from rules API (D-048)
        old_gh_d048 = gh
        old_pr_state_d048 = pr_state
        old_say_d048 = say
        old_time_sleep_d048 = time.sleep
        old_time_time_d048 = time.time
        old_required_checks_d048 = REQUIRED_CHECKS
        d048_messages = []
        def recording_say_d048(msg):
            d048_messages.append(msg)
            old_say_d048(msg)
        say = recording_say_d048

        try:
            # --- AC1 Scenario: Rules response names two required checks ---
            # 1. get_required_checks() reads exactly those two names
            two_checks_rules = [
                {
                    "type": "deletion",
                    "ruleset_id": 1,
                },
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "strict_required_status_checks_policy": False,
                        "required_status_checks": [
                            {"context": "Check Alpha"},
                            {"context": "Check Beta"},
                        ],
                    },
                },
            ]
            gh = lambda args, role=None, check=True: json.dumps(two_checks_rules)
            checks_ac1 = get_required_checks()
            assert checks_ac1 == {"Check Alpha", "Check Beta"}, f"Expected exactly two checks, got {checks_ac1}"

            # 2. wait_for_ci() waits for exactly those two names:
            # - Check Alpha SUCCESS alone does not finish (times out / keeps waiting)
            # - Check Alpha SUCCESS and Check Extra SUCCESS does not finish
            # - Check Alpha SUCCESS and Check Beta SUCCESS finishes immediately
            # - Check Alpha SUCCESS, Check Beta SUCCESS, and Check Extra PENDING finishes immediately
            poll_count_d048 = 0
            pr_state_checks = []
            def mock_pr_state_ac1(pr_num):
                nonlocal poll_count_d048
                poll_count_d048 += 1
                return {
                    "headRefOid": "d048head",
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                    "statusCheckRollup": pr_state_checks,
                    "body": "Refs #189",
                }
            pr_state = mock_pr_state_ac1

            sim_time = 1000.0
            time.time = lambda: sim_time
            def mock_sleep_d048(s):
                nonlocal sim_time
                sim_time += s
            time.sleep = mock_sleep_d048

            # Sub-scenario 1: Only Check Alpha reports SUCCESS -> times out without completing
            pr_state_checks = [{"name": "Check Alpha", "conclusion": "SUCCESS"}]
            sim_time = 1000.0
            timeout_raised_ac1 = False
            try:
                wait_for_ci(189, required_checks=checks_ac1, timeout=40)
            except Stop as exc:
                timeout_raised_ac1 = True
                assert exc.status == 1
            assert timeout_raised_ac1, "wait_for_ci did not wait for Check Beta when only Check Alpha reported SUCCESS"

            # Sub-scenario 2: Check Alpha and unrequired Check Extra report SUCCESS -> times out without completing
            pr_state_checks = [
                {"name": "Check Alpha", "conclusion": "SUCCESS"},
                {"name": "Check Extra", "conclusion": "SUCCESS"},
            ]
            sim_time = 1000.0
            timeout_raised_extra = False
            try:
                wait_for_ci(189, required_checks=checks_ac1, timeout=40)
            except Stop as exc:
                timeout_raised_extra = True
                assert exc.status == 1
            assert timeout_raised_extra, "wait_for_ci did not wait for Check Beta when extra unrequired check reported SUCCESS"

            # Sub-scenario 3: Exactly Check Alpha and Check Beta report SUCCESS -> succeeds immediately
            pr_state_checks = [
                {"name": "Check Alpha", "conclusion": "SUCCESS"},
                {"name": "Check Beta", "conclusion": "SUCCESS"},
            ]
            sim_time = 1000.0
            res_ac1 = wait_for_ci(189, required_checks=checks_ac1, timeout=40)
            assert res_ac1 is True, "wait_for_ci did not succeed when both required checks reported SUCCESS"

            # Sub-scenario 4: Check Alpha and Check Beta report SUCCESS while Check Extra also reports SUCCESS -> succeeds
            pr_state_checks = [
                {"name": "Check Alpha", "conclusion": "SUCCESS"},
                {"name": "Check Beta", "conclusion": "SUCCESS"},
                {"name": "Check Extra", "conclusion": "SUCCESS"},
            ]
            sim_time = 1000.0
            res_ac1_extra = wait_for_ci(189, required_checks=checks_ac1, timeout=40)
            assert res_ac1_extra is True, "wait_for_ci failed with required and unrequired success"

            print("test_d048_ac1_waits_for_exact_rule_check_names: OK")

            # --- AC2 Scenario: Response without required_status_checks rule, or failed request ---
            # Create a clean local repo for testing main() invocations
            d048_origin = os.path.join(tmpdir, "d048_origin")
            d048_local = os.path.join(tmpdir, "d048_local")
            os.makedirs(d048_origin)
            subprocess.run(["git", "init", "-q", "-b", "main", d048_origin], check=True)
            subprocess.run(["git", "-C", d048_origin, "config", "user.name", "Pipeline Test"], check=True)
            subprocess.run(["git", "-C", d048_origin, "config", "user.email", "pipeline-test@example.com"], check=True)
            subprocess.run(["git", "-C", d048_origin, "commit", "-q", "--allow-empty", "-m", "Initial commit"], check=True)
            subprocess.run(["git", "clone", "-q", d048_origin, d048_local], check=True)
            subprocess.run(["git", "-C", d048_local, "config", "user.name", "Pipeline Test"], check=True)
            subprocess.run(["git", "-C", d048_local, "config", "user.email", "pipeline-test@example.com"], check=True)

            # Sub-case 2a: Rules response without a required_status_checks rule
            d048_messages.clear()
            no_rule_response = [{"type": "deletion"}, {"type": "pull_request"}]
            gh = lambda args, role=None, check=True: json.dumps(no_rule_response)
            exit_code_no_rule = main(["189", "--role", "devops"], cwd=d048_local,
                                     run_dir=os.path.join(tmpdir, "d048_run_no_rule"))
            assert exit_code_no_rule == 1, f"Expected exit status 1 on missing rule, got {exit_code_no_rule}"
            assert any("rules on main" in m for m in d048_messages), f"Message did not name rules on main: {d048_messages}"

            # Sub-case 2b: Rules response with required_status_checks rule naming no checks
            d048_messages.clear()
            empty_checks_response = [{"type": "required_status_checks", "parameters": {"required_status_checks": []}}]
            gh = lambda args, role=None, check=True: json.dumps(empty_checks_response)
            exit_code_empty = main(["189", "--role", "devops"], cwd=d048_local,
                                   run_dir=os.path.join(tmpdir, "d048_run_empty"))
            assert exit_code_empty == 1, f"Expected exit status 1 on empty checks, got {exit_code_empty}"
            assert any("rules on main" in m for m in d048_messages), f"Message did not name rules on main: {d048_messages}"

            # Sub-case 2c: Failed request (gh raises exception / Stop(1))
            d048_messages.clear()
            def failing_gh(args, role=None, check=True):
                raise Stop(1, "command failed (1): gh api repos/... (HTTP 500)")
            gh = failing_gh
            exit_code_failed = main(["189", "--role", "devops"], cwd=d048_local,
                                    run_dir=os.path.join(tmpdir, "d048_run_failed"))
            assert exit_code_failed == 1, f"Expected exit status 1 on failed request, got {exit_code_failed}"
            assert any("rules on main" in m for m in d048_messages), f"Message did not name rules on main: {d048_messages}"

            print("test_d048_ac2_stops_exit_1_on_missing_or_failed_rules: OK")

        finally:
            gh = old_gh_d048
            pr_state = old_pr_state_d048
            say = old_say_d048
            time.sleep = old_time_sleep_d048
            time.time = old_time_time_d048
            REQUIRED_CHECKS = old_required_checks_d048

        # Test 12: AC1 and AC2 verification of deduplication of issue refs and role line in PR descriptions (D-049)
        test_issue = 190

        # AC1: Author summary with Refs #<issue> twice and a Role: line
        raw_summary_ac1 = (
            f"Refs #{test_issue}\n\n"
            "Summary paragraph 1 explaining the change.\n\n"
            "Summary paragraph 2 with details:\n"
            "- Item A\n"
            "- Item B\n\n"
            f"Refs #{test_issue}\n\n"
            f"Role: DevOps · Model: {AUTHOR_MODEL}"
        )

        body_ac1_devops = build_pr_body("devops", test_issue, raw_summary_ac1)
        # AC1 post-conditions:
        # - Contains Refs #<issue> exactly once
        assert body_ac1_devops.count(f"Refs #{test_issue}") == 1, (
            f"Expected Refs #{test_issue} exactly once, found {body_ac1_devops.count(f'Refs #{test_issue}')} times:\n{body_ac1_devops}"
        )
        assert body_ac1_devops.startswith(f"Refs #{test_issue}\n\n"), "PR body must start with Refs #<issue>"
        # - Contains exactly one line starting with Role:
        role_lines_ac1 = [line for line in body_ac1_devops.splitlines() if line.lstrip().startswith("Role:")]
        assert len(role_lines_ac1) == 1, (
            f"Expected exactly 1 line starting with Role:, found {len(role_lines_ac1)}: {role_lines_ac1}"
        )
        assert role_lines_ac1[0] == f"Role: DevOps · Model: {AUTHOR_MODEL}"
        assert body_ac1_devops.endswith(f"Role: DevOps · Model: {AUTHOR_MODEL}")
        # - Body content is preserved
        assert "Summary paragraph 1 explaining the change." in body_ac1_devops
        assert "- Item A" in body_ac1_devops

        # Also verify developer role
        body_ac1_dev = build_pr_body("developer", test_issue, raw_summary_ac1)
        assert body_ac1_dev.count(f"Refs #{test_issue}") == 1
        dev_role_lines = [line for line in body_ac1_dev.splitlines() if line.lstrip().startswith("Role:")]
        assert len(dev_role_lines) == 1
        assert dev_role_lines[0] == f"Role: Developer · Model: {AUTHOR_MODEL}"

        print("test_d049_ac1_pr_description_single_refs_and_role: OK")

        # AC2: Author summary with neither Refs #<issue> nor Role: line
        raw_summary_ac2 = (
            "Clean author summary without refs or role.\n\n"
            "Paragraph two with bullet points:\n"
            "- Step 1\n"
            "- Step 2\n\n"
            "Did not check:\n"
            "Nothing."
        )

        # Before the change, the body was constructed as:
        expected_body_before = (
            f"Refs #{test_issue}\n\n{raw_summary_ac2}\n\n"
            "The branch, commit, and this pull request were made by the "
            "pipeline from the author's working tree, after checking the changed paths against the role.\n\n"
            f"Role: DevOps · Model: {AUTHOR_MODEL}"
        )

        body_ac2 = build_pr_body("devops", test_issue, raw_summary_ac2)
        assert body_ac2 == expected_body_before, f"AC2 failed: description differs from before the change:\n{body_ac2}"

        # Single-line summary with neither
        single_summary = "Single line summary."
        expected_single = (
            f"Refs #{test_issue}\n\n{single_summary}\n\n"
            "The branch, commit, and this pull request were made by the "
            "pipeline from the author's working tree, after checking the changed paths against the role.\n\n"
            f"Role: Developer · Model: {AUTHOR_MODEL}"
        )
        assert build_pr_body("developer", test_issue, single_summary) == expected_single

        # Summary mentioning another issue is preserved
        other_summary = "Refs #999\n\nFix related to other issue."
        expected_other = (
            f"Refs #{test_issue}\n\n{other_summary}\n\n"
            "The branch, commit, and this pull request were made by the "
            "pipeline from the author's working tree, after checking the changed paths against the role.\n\n"
            f"Role: DevOps · Model: {AUTHOR_MODEL}"
        )
        assert build_pr_body("devops", test_issue, other_summary) == expected_other

        # Summary mentioning this issue in prose is preserved
        prose_summary = f"This commit resolves the issue described in Refs #{test_issue}."
        expected_prose = (
            f"Refs #{test_issue}\n\n{prose_summary}\n\n"
            "The branch, commit, and this pull request were made by the "
            "pipeline from the author's working tree, after checking the changed paths against the role.\n\n"
            f"Role: DevOps · Model: {AUTHOR_MODEL}"
        )
        assert build_pr_body("devops", test_issue, prose_summary) == expected_prose

        print("test_d049_ac2_pr_description_unchanged_when_neither_present: OK")

        # Round comment check (clean summary and single Role: line)
        round_comment_ac1 = build_round_comment("devops", test_issue, raw_summary_ac1)
        assert f"Refs #{test_issue}" not in round_comment_ac1
        comment_roles = [line for line in round_comment_ac1.splitlines() if line.lstrip().startswith("Role:")]
        assert len(comment_roles) == 1
        assert comment_roles[0] == f"Role: DevOps · Model: {AUTHOR_MODEL}"
        assert round_comment_ac1.endswith(f"Role: DevOps · Model: {AUTHOR_MODEL}")

        round_comment_ac2 = build_round_comment("developer", test_issue, raw_summary_ac2)
        assert round_comment_ac2 == f"{raw_summary_ac2}\n\nRole: Developer · Model: {AUTHOR_MODEL}"

        print("test_d049_round_comment_clean_and_single_role: OK")

        # Test 13: Failure condition on checks (D-050, updated by D-062)
        old_pr_state_d050 = pr_state
        old_time_sleep_d050 = time.sleep
        old_time_time_d050 = time.time
        try:
            d050_checks = []
            def mock_pr_state_d050(pr_num):
                return {
                    "headRefOid": "d050head",
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                    "statusCheckRollup": d050_checks,
                    "body": "Refs #194",
                }
            pr_state = mock_pr_state_d050

            sim_time_d050 = 1000.0
            time.time = lambda: sim_time_d050
            def mock_sleep_d050(s):
                nonlocal sim_time_d050
                sim_time_d050 += s
            time.sleep = mock_sleep_d050

            test_required = {"Required Gate 1", "Required Gate 2"}

            # AC1 (updated by D-062): A non-required check failed -> reported as CI failure (False)
            d050_checks = [
                {"name": "Required Gate 1", "conclusion": "SUCCESS"},
                {"name": "Required Gate 2", "conclusion": "SUCCESS"},
                {"name": "Optional Advisory Check", "conclusion": "FAILURE"},
            ]
            sim_time_d050 = 1000.0
            res_ac1 = wait_for_ci(194, required_checks=test_required, timeout=40)
            assert res_ac1 is False, f"wait_for_ci returned {res_ac1} instead of False when unrequired check failed"

            # Failure states on unrequired check (CANCELLED, TIMED_OUT, ERROR, ACTION_REQUIRED)
            for failure_state in ("CANCELLED", "TIMED_OUT", "ERROR", "ACTION_REQUIRED"):
                d050_checks = [
                    {"name": "Required Gate 1", "conclusion": "SUCCESS"},
                    {"name": "Required Gate 2", "conclusion": "SUCCESS"},
                    {"name": "Optional Advisory Check", "conclusion": failure_state},
                ]
                sim_time_d050 = 1000.0
                res_variant = wait_for_ci(194, required_checks=test_required, timeout=40)
                assert res_variant is False, f"wait_for_ci returned {res_variant} for unrequired state {failure_state}"

            print("test_d050_ac1_unrequired_check_failure_fails: OK")

            # AC2: A required check failed -> reports checks as failed (False)
            d050_checks = [
                {"name": "Required Gate 1", "conclusion": "SUCCESS"},
                {"name": "Required Gate 2", "conclusion": "FAILURE"},
                {"name": "Optional Advisory Check", "conclusion": "SUCCESS"},
            ]
            sim_time_d050 = 1000.0
            res_ac2 = wait_for_ci(194, required_checks=test_required, timeout=40)
            assert res_ac2 is False, f"AC2 failed: wait_for_ci returned {res_ac2} instead of False when required check failed"

            # AC2 variant: Other failure states for required check
            for failure_state in ("CANCELLED", "TIMED_OUT", "ERROR", "ACTION_REQUIRED"):
                d050_checks = [
                    {"name": "Required Gate 1", "conclusion": failure_state},
                    {"name": "Required Gate 2", "conclusion": "SUCCESS"},
                ]
                sim_time_d050 = 1000.0
                res_ac2_state = wait_for_ci(194, required_checks=test_required, timeout=40)
                assert res_ac2_state is False, f"AC2 failed for required check in state {failure_state}"

            # AC2 variant: Required check failed and unrequired check also failed
            d050_checks = [
                {"name": "Required Gate 1", "conclusion": "FAILURE"},
                {"name": "Required Gate 2", "conclusion": "SUCCESS"},
                {"name": "Optional Advisory Check", "conclusion": "FAILURE"},
            ]
            sim_time_d050 = 1000.0
            res_ac2_both = wait_for_ci(194, required_checks=test_required, timeout=40)
            assert res_ac2_both is False, "AC2 failed when both required and unrequired check failed"

            # AC2 variant: Context-based check reporting
            d050_checks = [
                {"context": "Required Gate 1", "state": "SUCCESS"},
                {"context": "Required Gate 2", "state": "FAILURE"},
            ]
            sim_time_d050 = 1000.0
            res_ac2_context = wait_for_ci(194, required_checks=test_required, timeout=40)
            assert res_ac2_context is False, "AC2 failed for context-based status failure"

            print("test_d050_ac2_required_check_failure_fails: OK")

        finally:
            pr_state = old_pr_state_d050
            time.sleep = old_time_sleep_d050
            time.time = old_time_time_d050

        # Test 14: AC1 to AC6 verification of continuing an issue from GitHub state (D-051)
        d051_origin = os.path.join(tmpdir, "d051_origin")
        d051_local = os.path.join(tmpdir, "d051_local")
        os.makedirs(d051_origin)
        subprocess.run(["git", "init", "-q", "-b", "main", d051_origin], check=True)
        subprocess.run(["git", "-C", d051_origin, "config", "user.name", "Pipeline Test"], check=True)
        subprocess.run(["git", "-C", d051_origin, "config", "user.email", "pipeline-test@example.com"], check=True)
        subprocess.run(["git", "-C", d051_origin, "config", "receive.denyCurrentBranch", "ignore"], check=True)
        subprocess.run(["git", "-C", d051_origin, "commit", "-q", "--allow-empty", "-m", "Initial commit"], check=True)

        subprocess.run(["git", "clone", "-q", d051_origin, d051_local], check=True)
        subprocess.run(["git", "-C", d051_local, "config", "user.name", "Pipeline Test"], check=True)
        subprocess.run(["git", "-C", d051_local, "config", "user.email", "pipeline-test@example.com"], check=True)

        mock_agent_record = os.path.join(tmpdir, "d051_agent_records.jsonl")
        mock_change_file = os.path.join(d051_local, "change.txt")
        mock_reviewer_flag = os.path.join(tmpdir, "d051_reviewer_flag")

        mock_agent_runner = os.path.join(tmpdir, "d051_agent_runner.py")
        with open(mock_agent_runner, "w") as f:
            f.write(r"""#!/usr/bin/env python3
import sys, json, os

role = sys.argv[1]
task = sys.argv[3] if len(sys.argv) > 3 else ""

record_file = os.environ.get("D051_RECORD_FILE")
if record_file:
    with open(record_file, "a") as f:
        f.write(json.dumps({"role": role, "task": task}) + "\n")

if role in ("developer", "devops"):
    change_file = os.environ.get("D051_CHANGE_FILE")
    if change_file:
        with open(change_file, "a") as f:
            f.write("change\n")
    print("PR-Title: D-051 test deliver\nPR-Summary: Author delivered changes")
elif role == "reviewer":
    rev_flag = os.environ.get("D051_REVIEWER_FLAG")
    if rev_flag:
        with open(rev_flag, "w") as f:
            f.write("reviewed\n")
sys.exit(0)
""")
        os.chmod(mock_agent_runner, 0o755)

        mock_token_sh = os.path.join(tmpdir, "d051_token.sh")
        with open(mock_token_sh, "w") as f:
            f.write("#!/bin/sh\necho mock_token\n")
        os.chmod(mock_token_sh, 0o755)

        mock_guard_py = os.path.join(tmpdir, "d051_guard.py")
        with open(mock_guard_py, "w") as f:
            f.write("#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n")
        os.chmod(mock_guard_py, 0o755)

        d051_tooling = Tooling(
            run_dir=tmpdir,
            tooling_dir=tmpdir,
            agent_run=mock_agent_runner,
            agent_token=mock_token_sh,
            role_guard=mock_guard_py,
            agents_dir=tooling_root.agents_dir,
        )

        class MockGHState:
            def __init__(self):
                self.issue_info = {}
                self.open_prs = []
                self.merged_prs = []
                self.pr_views = {}
                self.issue_comments = {}

        gh_state = MockGHState()

        def mock_gh_d051(args, role=None, check=True):
            if len(args) >= 2 and args[0] == "api" and "rules/branches/main" in args[1]:
                return json.dumps([{
                    "type": "required_status_checks",
                    "parameters": {"required_status_checks": [{"context": "test-ci"}]}
                }])
            if len(args) >= 2 and args[0] == "api" and "users/nextoomio-" in args[1]:
                return "12345"
            if len(args) >= 2 and args[0] == "issue" and args[1] == "view":
                issue_num = int(args[2])
                if "--json" in args:
                    fields = args[args.index("--json") + 1].split(",")
                    if "comments" in fields:
                        return json.dumps({"comments": gh_state.issue_comments.get(issue_num, [
                            {"author": {"login": "nextoomio-qm[bot]"}, "createdAt": "2999-01-01T00:00:00Z",
                             "body": "QM-Verdict: PASS\nVerified-Commit: " + subprocess.check_output(
                                 ["git", "-C", d051_local, "rev-parse", "main"], text=True).strip()}
                        ])})
                    if "body" in fields and len(fields) == 1:
                        return json.dumps({"body": gh_state.issue_info.get(issue_num, {}).get("body", "- [ ] **AC1**")})
                    return json.dumps(gh_state.issue_info.get(issue_num, {}))
            if len(args) >= 2 and args[0] == "pr" and args[1] == "list":
                state = "open"
                if "--state" in args:
                    state = args[args.index("--state") + 1]
                if state == "open":
                    return json.dumps(gh_state.open_prs)
                elif state == "merged":
                    return json.dumps(gh_state.merged_prs)
                return "[]"
            if len(args) >= 2 and args[0] == "pr" and args[1] == "view":
                pr_num = int(args[2])
                view_fn = gh_state.pr_views.get(pr_num)
                if callable(view_fn):
                    return json.dumps(view_fn(pr_num))
                return json.dumps(gh_state.pr_views.get(pr_num, {}))
            if len(args) >= 2 and args[0] == "pr" and args[1] == "create":
                return "https://github.com/nextoo-ai/nextoomio/pull/2050"
            return "{}"

        old_gh_d051 = gh
        old_tooling_d051 = TOOLING
        old_pr_state_d051 = pr_state
        old_required_checks_d051 = REQUIRED_CHECKS
        REQUIRED_CHECKS = {"test-ci"}

        try:
            TOOLING = d051_tooling
            gh = mock_gh_d051
            os.environ["D051_RECORD_FILE"] = mock_agent_record
            os.environ["D051_CHANGE_FILE"] = mock_change_file
            os.environ["D051_REVIEWER_FLAG"] = mock_reviewer_flag

            def get_invocations():
                if not os.path.exists(mock_agent_record):
                    return []
                with open(mock_agent_record, "r") as f:
                    return [json.loads(line) for line in f if line.strip()]

            # --- AC1: Open PR with no Reviewer verdict on head commit ---
            subprocess.run(["git", "-C", d051_origin, "branch", "-f", "ops/D-051-ac1-branch", "main"], check=True)
            subprocess.run(["git", "-C", d051_local, "switch", "-q", "main"], check=True)
            head_ac1 = subprocess.run(["git", "-C", d051_origin, "rev-parse", "ops/D-051-ac1-branch"],
                                      capture_output=True, text=True, check=True).stdout.strip()

            gh_state.issue_info[201] = {
                "title": "D-051 AC1 test issue",
                "state": "OPEN",
                "labels": [{"name": "state:ready"}, {"name": "area:tooling"}],
                "body": "### Touches\n- `scripts/agent-pipeline.py`\n",
            }
            gh_state.open_prs = [
                {
                    "number": 2019,
                    "headRefName": "ops/D-051-foreign",
                    "headRefOid": head_ac1,
                    "author": {"login": "untrusted-user"},
                    "body": "Refs #201\n\nAttacker PR",
                },
                {
                    "number": 2010,
                    "headRefName": "ops/D-051-ac1-branch",
                    "headRefOid": head_ac1,
                    "author": {"login": "nextoomio-devops[bot]"},
                    "body": "Refs #201\n\nOpen PR with no verdict yet",
                },
            ]
            assert find_pr(201, "devops")["number"] == 2010, "find_pr did not select role app PR"
            assert find_pr(201, "developer") is None, "find_pr matched wrong role app"
            gh_state.merged_prs = []

            def pr_view_2010(num):
                if os.path.exists(mock_reviewer_flag):
                    return {
                        "state": "MERGED",
                        "headRefOid": head_ac1,
                        "mergeStateStatus": "CLEAN",
                        "mergeable": "MERGEABLE",
                        "reviews": [{"author": {"login": "nextoomio-reviewer"}, "commit": {"oid": head_ac1},
                                    "state": "APPROVED", "body": "Completes-Story: yes"}],
                        "statusCheckRollup": [{"name": "test-ci", "conclusion": "SUCCESS"}],
                        "body": "Refs #201",
                    }
                return {
                    "state": "OPEN",
                    "headRefOid": head_ac1,
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                    "reviews": [],
                    "statusCheckRollup": [{"name": "test-ci", "conclusion": "SUCCESS"}],
                    "body": "Refs #201",
                }
            gh_state.pr_views[2010] = pr_view_2010

            if os.path.exists(mock_agent_record):
                os.remove(mock_agent_record)
            if os.path.exists(mock_reviewer_flag):
                os.remove(mock_reviewer_flag)

            exit_ac1 = main(["201", "--role", "devops"], cwd=d051_local,
                            run_dir=os.path.join(tmpdir, "d051_run_ac1"))
            assert exit_ac1 == 0, f"Expected exit status 0 for AC1, got {exit_ac1}"

            invocations_ac1 = get_invocations()
            roles_ac1 = [inv["role"] for inv in invocations_ac1]
            assert len(roles_ac1) >= 1, "No roles started in AC1"
            assert roles_ac1[0] == "reviewer", f"AC1 failed: first role started was not reviewer: {roles_ac1}"
            assert "devops" not in roles_ac1 and "developer" not in roles_ac1, f"AC1 failed: author was started: {roles_ac1}"
            assert "qm" in roles_ac1, f"AC1 failed: QM was not started after merge: {roles_ac1}"
            print("test_d051_ac1_open_pr_no_verdict_starts_reviewer_first: OK")

            # --- AC2: Open PR whose head commit has CHANGES_REQUESTED ---
            subprocess.run(["git", "-C", d051_origin, "branch", "-f", "ops/D-051-ac2-branch", "main"], check=True)
            subprocess.run(["git", "-C", d051_local, "switch", "-q", "main"], check=True)
            head_ac2 = subprocess.run(["git", "-C", d051_origin, "rev-parse", "ops/D-051-ac2-branch"],
                                      capture_output=True, text=True, check=True).stdout.strip()

            gh_state.issue_info[202] = {
                "title": "D-051 AC2 test issue",
                "state": "OPEN",
                "labels": [{"name": "state:ready"}, {"name": "area:tooling"}],
                "body": "### Touches\n- `scripts/agent-pipeline.py`\n",
            }
            gh_state.open_prs = [{
                "number": 2020,
                "headRefName": "ops/D-051-ac2-branch",
                "headRefOid": head_ac2,
                "author": {"login": "nextoomio-devops[bot]"},
                "body": "Refs #202\n\nOpen PR needing changes",
            }]

            def pr_view_2020(num):
                curr_head = subprocess.run(["git", "-C", d051_origin, "rev-parse", "ops/D-051-ac2-branch"],
                                           capture_output=True, text=True).stdout.strip()
                if os.path.exists(mock_reviewer_flag):
                    return {
                        "state": "MERGED",
                        "headRefOid": curr_head,
                        "mergeStateStatus": "CLEAN",
                        "mergeable": "MERGEABLE",
                        "reviews": [{"author": {"login": "nextoomio-reviewer"}, "commit": {"oid": curr_head},
                                    "state": "APPROVED", "body": "Completes-Story: yes"}],
                        "statusCheckRollup": [{"name": "test-ci", "conclusion": "SUCCESS"}],
                        "body": "Refs #202",
                    }
                return {
                    "state": "OPEN",
                    "headRefOid": curr_head,
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                    "reviews": [{"author": {"login": "nextoomio-reviewer"}, "commit": {"oid": head_ac2},
                                "state": "CHANGES_REQUESTED", "body": "Please fix AC2 issue"}],
                    "statusCheckRollup": [{"name": "test-ci", "conclusion": "SUCCESS"}],
                    "body": "Refs #202",
                }
            gh_state.pr_views[2020] = pr_view_2020

            if os.path.exists(mock_agent_record):
                os.remove(mock_agent_record)
            if os.path.exists(mock_reviewer_flag):
                os.remove(mock_reviewer_flag)
            if os.path.exists(mock_change_file):
                os.remove(mock_change_file)

            exit_ac2 = main(["202", "--role", "devops"], cwd=d051_local,
                            run_dir=os.path.join(tmpdir, "d051_run_ac2"))
            assert exit_ac2 == 0, f"Expected exit status 0 for AC2, got {exit_ac2}"

            invocations_ac2 = get_invocations()
            assert len(invocations_ac2) >= 1, "No roles started in AC2"
            assert invocations_ac2[0]["role"] == "devops", f"AC2 failed: author was not started first: {invocations_ac2}"
            task_ac2 = invocations_ac2[0]["task"]
            assert "Pull request 2020" in task_ac2, f"AC2 failed: task did not name pull request 2020: '{task_ac2}'"
            assert "needs changes" in task_ac2, f"AC2 failed: task did not state needs changes: '{task_ac2}'"
            print("test_d051_ac2_changes_requested_starts_author_with_pr_task: OK")

            # --- AC3: Open PR approved at its head commit ---
            subprocess.run(["git", "-C", d051_origin, "branch", "-f", "ops/D-051-ac3-branch", "main"], check=True)
            subprocess.run(["git", "-C", d051_local, "switch", "-q", "main"], check=True)
            head_ac3 = subprocess.run(["git", "-C", d051_origin, "rev-parse", "ops/D-051-ac3-branch"],
                                      capture_output=True, text=True, check=True).stdout.strip()

            gh_state.issue_info[203] = {
                "title": "D-051 AC3 test issue",
                "state": "OPEN",
                "labels": [{"name": "state:ready"}, {"name": "area:tooling"}],
                "body": "### Touches\n- `scripts/agent-pipeline.py`\n",
            }
            gh_state.open_prs = [{
                "number": 2030,
                "headRefName": "ops/D-051-ac3-branch",
                "headRefOid": head_ac3,
                "author": {"login": "nextoomio-devops[bot]"},
                "body": "Refs #203\n\nApproved PR",
            }]
            gh_state.pr_views[2030] = {
                "state": "MERGED",
                "headRefOid": head_ac3,
                "mergeStateStatus": "CLEAN",
                "mergeable": "MERGEABLE",
                "reviews": [{"author": {"login": "nextoomio-reviewer"}, "commit": {"oid": head_ac3},
                            "state": "APPROVED", "body": "Completes-Story: yes"}],
                "statusCheckRollup": [{"name": "test-ci", "conclusion": "SUCCESS"}],
                "body": "Refs #203",
            }

            if os.path.exists(mock_agent_record):
                os.remove(mock_agent_record)

            exit_ac3 = main(["203", "--role", "devops"], cwd=d051_local,
                            run_dir=os.path.join(tmpdir, "d051_run_ac3"))
            assert exit_ac3 == 0, f"Expected exit status 0 for AC3, got {exit_ac3}"

            invocations_ac3 = get_invocations()
            roles_ac3 = [inv["role"] for inv in invocations_ac3]
            assert "devops" not in roles_ac3 and "developer" not in roles_ac3, f"AC3 failed: author was started: {roles_ac3}"
            assert "reviewer" not in roles_ac3, f"AC3 failed: reviewer was started: {roles_ac3}"
            assert roles_ac3 == ["qm"], f"AC3 failed: expected only QM, got {roles_ac3}"
            print("test_d051_ac3_approved_pr_skips_author_and_reviewer_and_merges: OK")

            # --- AC4: Open issue with state:implemented and a merged pull request referencing it ---
            subprocess.run(["git", "-C", d051_local, "switch", "-q", "main"], check=True)
            gh_state.issue_info[204] = {
                "title": "D-051 AC4 test issue",
                "state": "OPEN",
                "labels": [{"name": "state:implemented"}, {"name": "area:tooling"}],
                "body": "### Touches\n- `scripts/agent-pipeline.py`\n",
            }
            gh_state.open_prs = []
            gh_state.merged_prs = [
                {
                    "number": 2049,
                    "author": {"login": "untrusted-user"},
                    "body": "Refs #204\n\nAttacker merged PR",
                },
                {
                    "number": 2040,
                    "author": {"login": "nextoomio-devops[bot]"},
                    "body": "Refs #204\n\nMerged PR for AC4",
                },
            ]
            assert find_merged_pr(204, "devops")["number"] == 2040, "find_merged_pr did not select role app PR"
            assert find_merged_pr(204, "developer") is None, "find_merged_pr matched wrong role app"

            if os.path.exists(mock_agent_record):
                os.remove(mock_agent_record)

            exit_ac4 = main(["204", "--role", "devops"], cwd=d051_local,
                            run_dir=os.path.join(tmpdir, "d051_run_ac4"))
            assert exit_ac4 == 0, f"Expected exit status 0 for AC4, got {exit_ac4}"

            invocations_ac4 = get_invocations()
            roles_ac4 = [inv["role"] for inv in invocations_ac4]
            assert len(roles_ac4) >= 1, "No roles started in AC4"
            assert roles_ac4[0] == "qm", f"AC4 failed: first role started was not QM: {roles_ac4}"
            assert "devops" not in roles_ac4 and "developer" not in roles_ac4, f"AC4 failed: author was started: {roles_ac4}"
            assert "reviewer" not in roles_ac4, f"AC4 failed: reviewer was started: {roles_ac4}"
            print("test_d051_ac4_implemented_issue_with_merged_pr_starts_qm_first: OK")

            # --- AC5: Issue with no pull request and state:ready ---
            subprocess.run(["git", "-C", d051_local, "switch", "-q", "main"], check=True)
            gh_state.issue_info[205] = {
                "title": "D-051 AC5 test issue",
                "state": "OPEN",
                "labels": [{"name": "state:ready"}, {"name": "area:tooling"}],
                "body": "### Touches\n- `scripts/agent-pipeline.py`\n",
            }
            gh_state.open_prs = []
            gh_state.merged_prs = []

            def pr_view_2050(num):
                curr_head = subprocess.run(["git", "-C", d051_origin, "rev-parse", "HEAD"],
                                           capture_output=True, text=True).stdout.strip()
                return {
                    "state": "MERGED",
                    "headRefOid": curr_head,
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                    "reviews": [{"author": {"login": "nextoomio-reviewer"}, "commit": {"oid": curr_head},
                                "state": "APPROVED", "body": "Completes-Story: yes"}],
                    "statusCheckRollup": [{"name": "test-ci", "conclusion": "SUCCESS"}],
                    "body": "Refs #205",
                }
            gh_state.pr_views[2050] = pr_view_2050

            if os.path.exists(mock_agent_record):
                os.remove(mock_agent_record)
            if os.path.exists(mock_change_file):
                os.remove(mock_change_file)

            exit_ac5 = main(["205", "--role", "devops"], cwd=d051_local,
                            run_dir=os.path.join(tmpdir, "d051_run_ac5"))
            assert exit_ac5 == 0, f"Expected exit status 0 for AC5, got {exit_ac5}"

            invocations_ac5 = get_invocations()
            assert len(invocations_ac5) >= 1, "No roles started in AC5"
            assert invocations_ac5[0]["role"] == "devops", f"AC5 failed: first role started was not devops: {invocations_ac5}"
            task_ac5 = invocations_ac5[0]["task"]
            assert "Work on issue 205" in task_ac5, f"AC5 failed: initial task unexpected: '{task_ac5}'"
            print("test_d051_ac5_ready_issue_no_pr_starts_author_first: OK")

            # --- AC6: All cases covered offline without network ---
            assert gh is mock_gh_d051, "AC6 failed: gh was not substituted with mock"
            assert TOOLING is d051_tooling, "AC6 failed: TOOLING was not substituted"
            assert TOOLING.agent_run == mock_agent_runner, "AC6 failed: mock_agent_runner was not used"
            origin_url = subprocess.run(["git", "-C", d051_local, "remote", "get-url", "origin"],
                                        capture_output=True, text=True, check=True).stdout.strip()
            assert os.path.isdir(origin_url), f"AC6 failed: origin is not a local directory: {origin_url}"
            assert not any(origin_url.startswith(p) for p in ("http://", "https://", "git@", "ssh://")), \
                f"AC6 failed: remote is not offline: {origin_url}"
            print("test_d051_ac6_offline_coverage: OK")

        finally:
            gh = old_gh_d051
            TOOLING = old_tooling_d051
            pr_state = old_pr_state_d051
            REQUIRED_CHECKS = old_required_checks_d051
            os.environ.pop("D051_RECORD_FILE", None)
            os.environ.pop("D051_CHANGE_FILE", None)
            os.environ.pop("D051_REVIEWER_FLAG", None)

        # Test 15: AC1, AC2, and AC4 verification of looking up pull requests by search query without fixed limits (D-052)
        old_gh_d052 = gh
        recorded_calls = []

        mock_responses = {
            "open": [],
            "merged": [],
        }

        def mock_gh_d052(args, role=None, check=True):
            recorded_calls.append(list(args))
            if len(args) >= 2 and args[0] == "pr" and args[1] == "list":
                state = "open"
                if "--state" in args:
                    state = args[args.index("--state") + 1]
                limit = 30
                if "--limit" in args:
                    limit = int(args[args.index("--limit") + 1])
                return json.dumps(mock_responses.get(state, [])[:limit])
            return "[]"

        try:
            gh = mock_gh_d052

            # --- AC1: find_merged_pr and find_pr query GitHub with --search and an explicit high --limit ---
            # Verified with >30 pull requests where matching PR is at the end (index 34)
            recorded_calls.clear()
            mock_merged_noise = [
                {
                    "number": 1000 + i,
                    "author": {"login": "unrelated-user"},
                    "body": f"Noise PR mentioning Refs #77 in a comment {i}",
                    "headRefName": f"noise-branch-{i}",
                    "headRefOid": f"noise_oid_{i}",
                }
                for i in range(34)
            ]
            mock_merged_target = {
                "number": 100,
                "author": {"login": "nextoomio-devops[bot]"},
                "body": "Refs #77\nMerged PR body",
                "headRefName": "ops/D-052-merged",
                "headRefOid": "abc1234",
            }
            mock_responses["merged"] = mock_merged_noise + [mock_merged_target]
            assert len(mock_responses["merged"]) == 35

            res_merged = find_merged_pr(77, "devops")
            assert res_merged is not None and res_merged["number"] == 100, \
                f"AC1 failed: find_merged_pr did not return matching PR (match past default 30 items): {res_merged}"
            assert len(recorded_calls) == 1, f"AC1 failed: expected 1 gh call, got {len(recorded_calls)}"
            args_merged = recorded_calls[0]
            assert "--search" in args_merged, f"AC1 failed: --search flag missing in args: {args_merged}"
            search_idx = args_merged.index("--search")
            assert args_merged[search_idx + 1] == "Refs #77", \
                f"AC1 failed: unexpected --search value: {args_merged[search_idx + 1]}"
            assert "--limit" in args_merged, \
                f"AC1 failed: --limit missing in args (must pass explicit high limit): {args_merged}"
            limit_val_merged = int(args_merged[args_merged.index("--limit") + 1])
            assert limit_val_merged >= len(mock_responses["merged"]), \
                f"AC1 failed: limit ({limit_val_merged}) is smaller than number of results needed ({len(mock_responses['merged'])})"
            assert limit_val_merged >= PR_SEARCH_LIMIT, \
                f"AC1 failed: limit ({limit_val_merged}) is less than PR_SEARCH_LIMIT ({PR_SEARCH_LIMIT})"
            assert "--state" in args_merged and args_merged[args_merged.index("--state") + 1] == "merged", \
                f"AC1 failed: expected --state merged: {args_merged}"

            recorded_calls.clear()
            mock_open_noise = [
                {
                    "number": 2000 + i,
                    "author": {"login": "unrelated-user"},
                    "body": f"Noise PR mentioning Refs #88 in discussion {i}",
                    "headRefName": f"noise-open-{i}",
                    "headRefOid": f"noise_oid_{i}",
                }
                for i in range(34)
            ]
            mock_open_target = {
                "number": 101,
                "author": {"login": "nextoomio-developer[bot]"},
                "body": "Refs #88\nOpen PR body",
                "headRefName": "dev/D-052-open",
                "headRefOid": "def5678",
            }
            mock_responses["open"] = mock_open_noise + [mock_open_target]
            assert len(mock_responses["open"]) == 35

            res_open = find_pr(88, "developer")
            assert res_open is not None and res_open["number"] == 101, \
                f"AC1 failed: find_pr did not return matching PR (match past default 30 items): {res_open}"
            assert len(recorded_calls) == 1, f"AC1 failed: expected 1 gh call, got {len(recorded_calls)}"
            args_open = recorded_calls[0]
            assert "--search" in args_open, f"AC1 failed: --search flag missing in open PR args: {args_open}"
            search_idx_open = args_open.index("--search")
            assert args_open[search_idx_open + 1] == "Refs #88", \
                f"AC1 failed: unexpected --search value: {args_open[search_idx_open + 1]}"
            assert "--limit" in args_open, \
                f"AC1 failed: --limit missing in open PR args: {args_open}"
            limit_val_open = int(args_open[args_open.index("--limit") + 1])
            assert limit_val_open >= len(mock_responses["open"]), \
                f"AC1 failed: limit ({limit_val_open}) smaller than number of results needed ({len(mock_responses['open'])})"
            assert limit_val_open >= PR_SEARCH_LIMIT, \
                f"AC1 failed: limit ({limit_val_open}) is less than PR_SEARCH_LIMIT ({PR_SEARCH_LIMIT})"
            assert "--state" in args_open and args_open[args_open.index("--state") + 1] == "open", \
                f"AC1 failed: expected --state open: {args_open}"

            print("test_d052_ac1_lookup_searches_issue_ref_with_high_limit: OK")

            # --- AC2: Search result with Refs #<n> and Refs #<n>0 picks only Refs #<n> ---
            # Test case A: order is [PR for <n>, PR for <n>0]
            pr_n = {
                "number": 501,
                "author": {"login": "nextoomio-devops[bot]"},
                "body": "Fixes bug.\n\nRefs #50\n\nSome more description",
                "headRefName": "ops/D-052-n",
                "headRefOid": "oid50",
            }
            pr_n0 = {
                "number": 502,
                "author": {"login": "nextoomio-devops[bot]"},
                "body": "Fixes other bug.\n\nRefs #500\n\nSome description",
                "headRefName": "ops/D-052-n0",
                "headRefOid": "oid500",
            }
            mock_responses["merged"] = [pr_n, pr_n0]
            picked_n = find_merged_pr(50, "devops")
            assert picked_n is not None and picked_n["number"] == 501, \
                f"AC2 failed: did not pick PR for issue 50 (got {picked_n})"
            picked_n0 = find_merged_pr(500, "devops")
            assert picked_n0 is not None and picked_n0["number"] == 502, \
                f"AC2 failed: did not pick PR for issue 500 (got {picked_n0})"

            # Test case B: order is inverted [PR for <n>0, PR for <n>]; looking for <n> must still pick only <n>
            mock_responses["merged"] = [pr_n0, pr_n]
            picked_inverted = find_merged_pr(50, "devops")
            assert picked_inverted is not None and picked_inverted["number"] == 501, \
                f"AC2 failed: inverted search order picked wrong PR: {picked_inverted}"

            # Test case C: same distinction applies to open PRs in find_pr()
            mock_responses["open"] = [pr_n0, pr_n]
            picked_open = find_pr(50, "devops")
            assert picked_open is not None and picked_open["number"] == 501, \
                f"AC2 failed: open PR lookup with inverted order picked wrong PR: {picked_open}"

            # Test case D: role isolation and untrusted PRs are still rejected
            pr_untrusted = {
                "number": 503,
                "author": {"login": "attacker"},
                "body": "Refs #50",
                "headRefName": "evil/branch",
                "headRefOid": "bad123",
            }
            pr_wrong_role = {
                "number": 504,
                "author": {"login": "nextoomio-developer[bot]"},
                "body": "Refs #50",
                "headRefName": "dev/branch",
                "headRefOid": "dev123",
            }
            mock_responses["merged"] = [pr_untrusted, pr_wrong_role]
            assert find_merged_pr(50, "devops") is None, "AC2 failed: untrusted or wrong-role PR was matched"

            print("test_d052_ac2_exact_issue_match_filters_out_longer_number_suffix: OK")

            # --- AC4: Substituted responses, no network ---
            assert gh is mock_gh_d052, "AC4 failed: gh was not substituted with mock"
            assert len(recorded_calls) > 0, "AC4 failed: no gh calls were recorded through mock"
            print("test_d052_ac4_offline_coverage: OK")

        finally:
            gh = old_gh_d052

        # Test 16: AC1 to AC3 verification of updating pull-request description on later rounds (D-054)
        # AC3: The round task text asks for a summary of the whole pull request at its head.
        task_review = round_author_task(234, 226, "the review requested changes")
        assert "Pull request 234 for issue 226 needs changes: the review requested changes." in task_review
        assert "gh pr view 234 --comments" in task_review
        assert "gh pr checks 234" in task_review
        assert "describes the whole pull request at its head" in task_review
        assert "(what the diff contains now, what was run on it)" in task_review
        assert "not only the changes of the round" in task_review
        assert "Follow your role definition." in task_review

        task_ci = round_author_task(234, 226, "CI failed on the head commit")
        assert "Pull request 234 for issue 226 needs changes: CI failed on the head commit." in task_ci
        assert "describes the whole pull request at its head" in task_ci

        print("test_d054_ac3_round_task_text_asks_for_summary_at_head: OK")

        # AC1 and AC2: deliver() updates description when PR-Summary is present; leaves unchanged when absent
        d054_origin = os.path.join(tmpdir, "d054_origin")
        d054_local = os.path.join(tmpdir, "d054_local")
        os.makedirs(d054_origin)
        subprocess.run(["git", "init", "-q", "-b", "main", d054_origin], check=True)
        subprocess.run(["git", "-C", d054_origin, "config", "user.name", "Pipeline Test"], check=True)
        subprocess.run(["git", "-C", d054_origin, "config", "user.email", "pipeline-test@example.com"], check=True)
        subprocess.run(["git", "-C", d054_origin, "config", "receive.denyCurrentBranch", "ignore"], check=True)
        subprocess.run(["git", "-C", d054_origin, "commit", "-q", "--allow-empty", "-m", "Initial commit"], check=True)

        subprocess.run(["git", "clone", "-q", d054_origin, d054_local], check=True)
        subprocess.run(["git", "-C", d054_local, "config", "user.name", "Pipeline Test"], check=True)
        subprocess.run(["git", "-C", d054_local, "config", "user.email", "pipeline-test@example.com"], check=True)

        mock_token_d054 = os.path.join(tmpdir, "d054_token.sh")
        with open(mock_token_d054, "w") as f:
            f.write("#!/bin/sh\necho mock_token\n")
        os.chmod(mock_token_d054, 0o755)

        mock_guard_d054 = os.path.join(tmpdir, "d054_guard.py")
        with open(mock_guard_d054, "w") as f:
            f.write("#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n")
        os.chmod(mock_guard_d054, 0o755)

        d054_tooling = Tooling(
            run_dir=tmpdir,
            tooling_dir=tmpdir,
            agent_run=tooling_root.agent_run,
            agent_token=mock_token_d054,
            role_guard=mock_guard_d054,
            agents_dir=tooling_root.agents_dir,
        )

        class MockGH_D054:
            def __init__(self):
                self.pr_descriptions = {
                    540: "Initial round 1 description\n\nRefs #237\n\nRole: DevOps · Model: old-model",
                    541: "Initial round 1 dev description\n\nRefs #237\n\nRole: Developer · Model: old-model",
                }
                self.pr_comments = []
                self.edit_calls = []

            def __call__(self, args, role=None, check=True):
                if len(args) >= 2 and args[0] == "api" and "users/nextoomio-" in args[1]:
                    return "12345"
                if len(args) >= 2 and args[0] == "pr" and args[1] == "edit":
                    pr_num = int(args[2])
                    body_idx = args.index("--body") + 1
                    new_body = args[body_idx]
                    self.pr_descriptions[pr_num] = new_body
                    self.edit_calls.append({"pr": pr_num, "body": new_body, "role": role})
                    return "{}"
                if len(args) >= 2 and args[0] == "pr" and args[1] == "comment":
                    pr_num = int(args[2])
                    body_idx = args.index("--body") + 1
                    comment_body = args[body_idx]
                    self.pr_comments.append({"pr": pr_num, "body": comment_body, "role": role})
                    return "{}"
                return "{}"

        mock_gh_d054 = MockGH_D054()
        old_gh_d054 = gh
        old_tooling_d054 = TOOLING

        try:
            gh = mock_gh_d054
            TOOLING = d054_tooling

            subprocess.run(["git", "-C", d054_local, "switch", "-q", "-c", "ops/D-054-test"], check=True)
            subprocess.run(["git", "-C", d054_local, "push", "-q", "-u", "origin", "ops/D-054-test"], check=True)

            # AC1: Given an open pull request made by the pipeline, when a later round
            # delivers a commit with a PR-Summary, then the pull-request description equals
            # build_pr_body of that summary (one Refs #<n> line, one role line) and a round comment is posted.
            with open(os.path.join(d054_local, "change1.txt"), "w") as f:
                f.write("change round 2\n")

            output_round2 = (
                "PR-Title: ops: updated round 2 changes\n"
                "PR-Summary:\n"
                "Refs #237\n\n"
                "This is the updated summary describing the whole pull request at its head.\n"
                "All items verified and covered by tests.\n\n"
                f"Role: DevOps · Model: {AUTHOR_MODEL}\n"
            )

            pr_ret1 = deliver("devops", 237, "D-054 test issue", "ops/D-054-test", output_round2, [], 540, cwd=d054_local)
            assert pr_ret1 == 540, f"Expected deliver to return PR number 540, got {pr_ret1}"

            # PR description updated
            assert len(mock_gh_d054.edit_calls) == 1
            assert mock_gh_d054.edit_calls[0]["pr"] == 540
            assert mock_gh_d054.edit_calls[0]["role"] == "devops"

            desc_after_r2 = mock_gh_d054.pr_descriptions[540]
            expected_r2_desc = build_pr_body("devops", 237, output_round2.split("PR-Summary:", 1)[1].strip())
            assert desc_after_r2 == expected_r2_desc, f"PR description does not match build_pr_body:\n{desc_after_r2}"
            assert desc_after_r2.startswith("Refs #237\n\n")
            assert desc_after_r2.count("Refs #237") == 1
            role_lines = [l for l in desc_after_r2.splitlines() if l.strip().startswith("Role:")]
            assert len(role_lines) == 1
            assert role_lines[0] == f"Role: DevOps · Model: {AUTHOR_MODEL}"
            assert desc_after_r2.endswith(f"Role: DevOps · Model: {AUTHOR_MODEL}")
            assert "This is the updated summary describing the whole pull request at its head." in desc_after_r2

            # Round comment posted
            assert len(mock_gh_d054.pr_comments) == 1
            comment_r2 = mock_gh_d054.pr_comments[0]
            assert comment_r2["pr"] == 540
            assert comment_r2["role"] == "devops"
            expected_r2_comment = build_round_comment("devops", 237, output_round2.split("PR-Summary:", 1)[1].strip())
            assert comment_r2["body"] == expected_r2_comment
            assert "Refs #237" not in comment_r2["body"]
            comment_role_lines = [l for l in comment_r2["body"].splitlines() if l.strip().startswith("Role:")]
            assert len(comment_role_lines) == 1
            assert comment_role_lines[0] == f"Role: DevOps · Model: {AUTHOR_MODEL}"

            # Also verify Developer role delivers with Developer role line
            with open(os.path.join(d054_local, "change_dev.txt"), "w") as f:
                f.write("change round 2 dev\n")
            output_dev = "PR-Title: dev: update\nPR-Summary: Dev summary for whole PR at head."
            deliver("developer", 237, "D-054 dev issue", "ops/D-054-test", output_dev, [], 541, cwd=d054_local)
            desc_dev = mock_gh_d054.pr_descriptions[541]
            assert desc_dev.count("Refs #237") == 1
            assert f"Role: Developer · Model: {AUTHOR_MODEL}" in desc_dev

            # Also verify conversational text mentioning PR-Summary before the final block does not leak into summary
            with open(os.path.join(d054_local, "change_conv.txt"), "w") as f:
                f.write("change round 2 conv\n")
            output_conv = (
                "Explanation: earlier discussion mentioning `PR-Summary:` in text.\n\n"
                "PR-Title: ops: test conv\n"
                "PR-Summary:\n"
                "Clean summary for whole PR at head.\n"
            )
            deliver("devops", 237, "D-054 conv issue", "ops/D-054-test", output_conv, [], 540, cwd=d054_local)
            desc_conv = mock_gh_d054.pr_descriptions[540]
            assert "Explanation: earlier discussion" not in desc_conv
            assert "Clean summary for whole PR at head." in desc_conv
            assert desc_conv.count("Refs #237") == 1

            print("test_d054_ac1_later_round_with_summary_updates_description_and_posts_comment: OK")

            # AC2: Given a later round whose output has no PR-Summary, then the description is unchanged.
            with open(os.path.join(d054_local, "change1.txt"), "a") as f:
                f.write("change round 3 without summary\n")

            desc_before_r3 = mock_gh_d054.pr_descriptions[540]
            edits_count_before = len(mock_gh_d054.edit_calls)
            comments_count_before = len(mock_gh_d054.pr_comments)

            output_no_summary = "PR-Title: ops: round 3 fixes\nI fixed the issue without writing PR-Summary keyword."
            pr_ret2 = deliver("devops", 237, "D-054 test issue", "ops/D-054-test", output_no_summary, [], 540, cwd=d054_local)
            assert pr_ret2 == 540

            # Description is unchanged (no edit called)
            assert len(mock_gh_d054.edit_calls) == edits_count_before, "gh pr edit was called when no summary was provided!"
            assert mock_gh_d054.pr_descriptions[540] == desc_before_r3, "PR description was modified!"

            # Round comment is posted and states that author gave no summary
            assert len(mock_gh_d054.pr_comments) == comments_count_before + 1
            comment_r3 = mock_gh_d054.pr_comments[-1]
            assert comment_r3["pr"] == 540
            assert comment_r3["role"] == "devops"
            assert "The author gave no summary; see the diff." in comment_r3["body"]
            assert comment_r3["body"].endswith(f"Role: DevOps · Model: {AUTHOR_MODEL}")

            # AC2 variant: PR-Summary present but contains only whitespace
            with open(os.path.join(d054_local, "change1.txt"), "a") as f:
                f.write("change round 4 whitespace summary\n")

            output_whitespace = "PR-Title: ops: round 4\nPR-Summary:   \n\t  \n"
            deliver("devops", 237, "D-054 test issue", "ops/D-054-test", output_whitespace, [], 540, cwd=d054_local)

            assert len(mock_gh_d054.edit_calls) == edits_count_before, "gh pr edit called for whitespace-only summary!"
            assert mock_gh_d054.pr_descriptions[540] == desc_before_r3, "PR description modified for whitespace-only summary!"
            comment_r4 = mock_gh_d054.pr_comments[-1]
            assert "The author gave no summary; see the diff." in comment_r4["body"]

            print("test_d054_ac2_later_round_no_summary_leaves_description_unchanged: OK")

            # Offline verification
            assert gh is mock_gh_d054
            assert TOOLING is d054_tooling
            print("test_d054_offline_coverage: OK")

        finally:
            gh = old_gh_d054
            TOOLING = old_tooling_d054

        # Test 17: AC1 to AC5 verification of conflict resolution, non-required checks, and PR-Summary parsing (D-062)
        # --- AC5: Given author output with PR-Summary: only inside a sentence, then description is unchanged ---
        d062_ac5_origin = os.path.join(tmpdir, "d062_ac5_origin")
        d062_ac5_local = os.path.join(tmpdir, "d062_ac5_local")
        os.makedirs(d062_ac5_origin)
        subprocess.run(["git", "init", "-q", "-b", "main", d062_ac5_origin], check=True)
        subprocess.run(["git", "-C", d062_ac5_origin, "config", "user.name", "Pipeline Test"], check=True)
        subprocess.run(["git", "-C", d062_ac5_origin, "config", "user.email", "pipeline-test@example.com"], check=True)
        subprocess.run(["git", "-C", d062_ac5_origin, "config", "receive.denyCurrentBranch", "ignore"], check=True)
        subprocess.run(["git", "-C", d062_ac5_origin, "commit", "-q", "--allow-empty", "-m", "Initial commit"], check=True)

        subprocess.run(["git", "clone", "-q", d062_ac5_origin, d062_ac5_local], check=True)
        subprocess.run(["git", "-C", d062_ac5_local, "config", "user.name", "Pipeline Test"], check=True)
        subprocess.run(["git", "-C", d062_ac5_local, "config", "user.email", "pipeline-test@example.com"], check=True)

        subprocess.run(["git", "-C", d062_ac5_local, "switch", "-q", "-c", "ops/D-062-ac5"], check=True)
        subprocess.run(["git", "-C", d062_ac5_local, "push", "-q", "-u", "origin", "ops/D-062-ac5"], check=True)

        mock_gh_d062_ac5 = MockGH_D054()
        mock_gh_d062_ac5.pr_descriptions[620] = "Original description before AC5\n\nRefs #247\n\nRole: DevOps · Model: model-1"
        old_gh_ac5 = gh
        old_tooling_ac5 = TOOLING

        try:
            gh = mock_gh_d062_ac5
            TOOLING = d054_tooling

            with open(os.path.join(d062_ac5_local, "file_ac5.txt"), "w") as f:
                f.write("change for ac5\n")

            output_inline_summary = (
                "Round done. I did not repeat the `PR-Summary:` block; see the diff.\n"
                "Everything was verified.\n"
            )

            pr_ret_ac5 = deliver("devops", 247, "D-062 test issue", "ops/D-062-ac5", output_inline_summary, [], 620, cwd=d062_ac5_local)
            assert pr_ret_ac5 == 620
            # gh pr edit was NOT called
            assert len(mock_gh_d062_ac5.edit_calls) == 0, f"gh pr edit was called: {mock_gh_d062_ac5.edit_calls}"
            assert mock_gh_d062_ac5.pr_descriptions[620] == "Original description before AC5\n\nRefs #247\n\nRole: DevOps · Model: model-1"
            # gh pr comment was called stating author gave no summary
            assert len(mock_gh_d062_ac5.pr_comments) == 1
            assert "The author gave no summary; see the diff." in mock_gh_d062_ac5.pr_comments[0]["body"]
            print("test_d062_ac5_inline_pr_summary_leaves_description_unchanged: OK")
        finally:
            gh = old_gh_ac5
            TOOLING = old_tooling_ac5

        # --- AC4: Given a head commit with a non-required check still in progress, Reviewer not started until completed or timeout ---
        old_pr_state_ac4 = pr_state
        old_sleep_ac4 = time.sleep
        old_time_ac4 = time.time
        try:
            sim_time_ac4 = 1000.0
            time.time = lambda: sim_time_ac4
            def mock_sleep_ac4(s):
                nonlocal sim_time_ac4
                sim_time_ac4 += s
            time.sleep = mock_sleep_ac4

            test_req = {"Required Gate"}

            # Scenario 4a: Non-required check IN_PROGRESS on poll 1, SUCCESS on poll 2 -> waits and succeeds
            poll_ac4 = 0
            def mock_pr_state_progressive_ac4(pr_num):
                nonlocal poll_ac4
                poll_ac4 += 1
                if poll_ac4 == 1:
                    return {
                        "headRefOid": "d062head",
                        "mergeStateStatus": "CLEAN",
                        "mergeable": "MERGEABLE",
                        "statusCheckRollup": [
                            {"name": "Required Gate", "conclusion": "SUCCESS"},
                            {"name": "Yocto (parse-only on ubuntu-24.04)", "status": "IN_PROGRESS"},
                        ],
                        "body": "Refs #247",
                    }
                return {
                    "headRefOid": "d062head",
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                    "statusCheckRollup": [
                        {"name": "Required Gate", "conclusion": "SUCCESS"},
                        {"name": "Yocto (parse-only on ubuntu-24.04)", "conclusion": "SUCCESS"},
                    ],
                    "body": "Refs #247",
                }
            pr_state = mock_pr_state_progressive_ac4
            sim_time_ac4 = 1000.0
            res_ac4_wait = wait_for_ci(247, required_checks=test_req, timeout=40)
            assert res_ac4_wait is True, "wait_for_ci did not succeed after non-required check completed"
            assert poll_ac4 == 2, f"Expected 2 polls (waited for Yocto), got {poll_ac4}"

            # Scenario 4b: Non-required check stays IN_PROGRESS until timeout -> raises Stop(1)
            pr_state = lambda pr_num: {
                "headRefOid": "d062head",
                "mergeStateStatus": "CLEAN",
                "mergeable": "MERGEABLE",
                "statusCheckRollup": [
                    {"name": "Required Gate", "conclusion": "SUCCESS"},
                    {"name": "Yocto (parse-only on ubuntu-24.04)", "status": "IN_PROGRESS"},
                ],
                "body": "Refs #247",
            }
            sim_time_ac4 = 1000.0
            timeout_raised_ac4 = False
            try:
                wait_for_ci(247, required_checks=test_req, timeout=40)
            except Stop as exc:
                timeout_raised_ac4 = True
                assert exc.status == 1
                assert "the checks on pull request #247 did not finish" in str(exc)
            assert timeout_raised_ac4, "wait_for_ci did not time out when non-required check stayed in progress"

            # Scenario 4c: Non-required check completes with FAILURE -> returns False (CI failure)
            pr_state = lambda pr_num: {
                "headRefOid": "d062head",
                "mergeStateStatus": "CLEAN",
                "mergeable": "MERGEABLE",
                "statusCheckRollup": [
                    {"name": "Required Gate", "conclusion": "SUCCESS"},
                    {"name": "Yocto (parse-only on ubuntu-24.04)", "conclusion": "FAILURE"},
                ],
                "body": "Refs #247",
            }
            sim_time_ac4 = 1000.0
            res_ac4_fail = wait_for_ci(247, required_checks=test_req, timeout=40)
            assert res_ac4_fail is False, "wait_for_ci did not report False when non-required check failed"

            print("test_d062_ac4_unrequired_check_handling: OK")
        finally:
            pr_state = old_pr_state_ac4
            time.sleep = old_sleep_ac4
            time.time = old_time_ac4

        # --- AC1, AC2, and AC3: Conflict resolution and clean merge handling ---
        d062_origin = os.path.join(tmpdir, "d062_origin")
        d062_local = os.path.join(tmpdir, "d062_local")
        os.makedirs(d062_origin)
        subprocess.run(["git", "init", "-q", "-b", "main", d062_origin], check=True)
        subprocess.run(["git", "-C", d062_origin, "config", "user.name", "Pipeline Test"], check=True)
        subprocess.run(["git", "-C", d062_origin, "config", "user.email", "pipeline-test@example.com"], check=True)
        subprocess.run(["git", "-C", d062_origin, "config", "receive.denyCurrentBranch", "ignore"], check=True)

        with open(os.path.join(d062_origin, "common.txt"), "w") as f:
            f.write("base content\n")
        subprocess.run(["git", "-C", d062_origin, "add", "common.txt"], check=True)
        subprocess.run(["git", "-C", d062_origin, "commit", "-q", "-m", "Initial commit"], check=True)

        subprocess.run(["git", "clone", "-q", d062_origin, d062_local], check=True)
        subprocess.run(["git", "-C", d062_local, "config", "user.name", "Pipeline Test"], check=True)
        subprocess.run(["git", "-C", d062_local, "config", "user.email", "pipeline-test@example.com"], check=True)

        mock_record_d062 = os.path.join(tmpdir, "d062_agent_records.jsonl")
        mock_runner_d062 = os.path.join(tmpdir, "d062_agent_runner.py")
        with open(mock_runner_d062, "w") as f:
            f.write(r"""#!/usr/bin/env python3
import sys, json, os

role = sys.argv[1]
task = sys.argv[3] if len(sys.argv) > 3 else ""
cwd = os.getcwd()

# Check if merge is in progress in working tree
merge_in_progress = os.path.exists(os.path.join(cwd, ".git", "MERGE_HEAD"))

record_file = os.environ.get("D062_RECORD_FILE")
if record_file:
    with open(record_file, "a") as f:
        f.write(json.dumps({"role": role, "task": task, "merge_in_progress": merge_in_progress}) + "\n")

if role in ("developer", "devops"):
    # If conflict markers in common.txt, resolve them
    common_path = os.path.join(cwd, "common.txt")
    if os.path.exists(common_path):
        with open(common_path, "r") as f:
            c_text = f.read()
        if "<<<<<<<" in c_text or "conflict" in c_text:
            with open(common_path, "w") as f:
                f.write("resolved content: main + story\n")
    for f_clean in ("branch_clean.txt", "branch_clean2.txt"):
        p_clean = os.path.join(cwd, f_clean)
        if os.path.exists(p_clean):
            with open(p_clean, "a") as f:
                f.write("author address changes\n")
    print("PR-Title: D-062 Resolved conflicts\nPR-Summary: Author resolved merge conflicts")
elif role == "reviewer":
    rev_flag = os.environ.get("D062_REVIEWER_FLAG")
    if rev_flag:
        with open(rev_flag, "w") as f:
            f.write("reviewed\n")
sys.exit(0)
""")
        os.chmod(mock_runner_d062, 0o755)

        d062_tooling = Tooling(
            run_dir=tmpdir,
            tooling_dir=tmpdir,
            agent_run=mock_runner_d062,
            agent_token=d051_tooling.agent_token,
            role_guard=tooling_root.role_guard,
            agents_dir=tooling_root.agents_dir,
        )

        gh_state_d062 = MockGHState()
        gh_blocked_labels = []
        def mock_gh_d062(args, role=None, check=True):
            if len(args) >= 2 and args[0] == "issue" and args[1] == "edit":
                if "--add-label" in args:
                    idx = args.index("--add-label") + 1
                    gh_blocked_labels.append(args[idx])
            if len(args) >= 2 and args[0] == "api" and "rules/branches/main" in args[1]:
                return json.dumps([{
                    "type": "required_status_checks",
                    "parameters": {"required_status_checks": [{"context": "test-ci"}]}
                }])
            if len(args) >= 2 and args[0] == "api" and "users/nextoomio-" in args[1]:
                return "12345"
            if len(args) >= 2 and args[0] == "issue" and args[1] == "view":
                issue_num = int(args[2])
                if "--json" in args:
                    fields = args[args.index("--json") + 1].split(",")
                    if "comments" in fields:
                        return json.dumps({"comments": [
                            {"author": {"login": "nextoomio-qm[bot]"}, "createdAt": "2999-01-01T00:00:00Z",
                             "body": "QM-Verdict: PASS\nVerified-Commit: " + subprocess.check_output(
                                 ["git", "-C", d062_local, "rev-parse", "main"], text=True).strip()}
                        ]})
                    if "body" in fields and len(fields) == 1:
                        return json.dumps({"body": "- [ ] **AC1**"})
                    return json.dumps(gh_state_d062.issue_info.get(issue_num, {}))
            if len(args) >= 2 and args[0] == "pr" and args[1] == "list":
                state = "open"
                if "--state" in args:
                    state = args[args.index("--state") + 1]
                if state == "open":
                    return json.dumps(gh_state_d062.open_prs)
                elif state == "merged":
                    return json.dumps(gh_state_d062.merged_prs)
                return "[]"
            if len(args) >= 2 and args[0] == "pr" and args[1] == "view":
                pr_num = int(args[2])
                view_fn = gh_state_d062.pr_views.get(pr_num)
                if callable(view_fn):
                    return json.dumps(view_fn(pr_num))
                return json.dumps(gh_state_d062.pr_views.get(pr_num, {}))
            return "{}"

        old_gh_d062 = gh
        old_tooling_d062 = TOOLING
        old_required_checks_d062 = REQUIRED_CHECKS
        REQUIRED_CHECKS = {"test-ci"}

        try:
            TOOLING = d062_tooling
            gh = mock_gh_d062
            os.environ["D062_RECORD_FILE"] = mock_record_d062

            def get_d062_records():
                if not os.path.exists(mock_record_d062):
                    return []
                with open(mock_record_d062, "r") as f:
                    return [json.loads(line) for line in f if line.strip()]

            # --- AC1: Open PR that conflicts with main ---
            # 1. On origin main, add core file outside devops role paths and modify common.txt
            os.makedirs(os.path.join(d062_origin, "core"), exist_ok=True)
            with open(os.path.join(d062_origin, "core", "main_core.rs"), "w") as f:
                f.write("pub fn core_from_main() {}\n")
            with open(os.path.join(d062_origin, "common.txt"), "w") as f:
                f.write("main branch content\n")
            subprocess.run(["git", "-C", d062_origin, "add", "-A"], check=True)
            subprocess.run(["git", "-C", d062_origin, "commit", "-q", "-m", "Commit on main with core and common"], check=True)

            # 2. Create PR branch from base with conflicting common.txt
            subprocess.run(["git", "-C", d062_origin, "branch", "-f", "ops/D-062-ac1-branch", "HEAD~1"], check=True)
            subprocess.run(["git", "-C", d062_local, "fetch", "-q", "origin"], check=True)
            subprocess.run(["git", "-C", d062_local, "switch", "-q", "-C", "ops/D-062-ac1-branch", "origin/ops/D-062-ac1-branch"], check=True)
            with open(os.path.join(d062_local, "common.txt"), "w") as f:
                f.write("pr branch content (conflict)\n")
            subprocess.run(["git", "-C", d062_local, "commit", "-q", "-a", "-m", "Commit on pr branch"], check=True)
            subprocess.run(["git", "-C", d062_local, "push", "-q", "-u", "origin", "ops/D-062-ac1-branch"], check=True)
            head_ac1_init = subprocess.run(["git", "-C", d062_origin, "rev-parse", "ops/D-062-ac1-branch"],
                                           capture_output=True, text=True, check=True).stdout.strip()

            subprocess.run(["git", "-C", d062_local, "switch", "-q", "main"], check=True)

            gh_state_d062.issue_info[261] = {
                "title": "D-062 AC1 issue",
                "state": "OPEN",
                "labels": [{"name": "state:ready"}, {"name": "area:tooling"}],
                "body": "### Touches\n- `common.txt`\n",
            }
            gh_state_d062.open_prs = [{
                "number": 2610,
                "headRefName": "ops/D-062-ac1-branch",
                "headRefOid": head_ac1_init,
                "author": {"login": "nextoomio-devops[bot]"},
                "body": "Refs #261\n\nOpen conflicting PR",
            }]

            def pr_view_2610(num):
                curr_head = subprocess.run(["git", "-C", d062_origin, "rev-parse", "ops/D-062-ac1-branch"],
                                           capture_output=True, text=True).stdout.strip()
                if curr_head != head_ac1_init:
                    return {
                        "state": "MERGED",
                        "headRefOid": curr_head,
                        "mergeStateStatus": "CLEAN",
                        "mergeable": "MERGEABLE",
                        "reviews": [{"author": {"login": "nextoomio-reviewer"}, "commit": {"oid": curr_head},
                                    "state": "APPROVED", "body": "Completes-Story: yes"}],
                        "statusCheckRollup": [{"name": "test-ci", "conclusion": "SUCCESS"}],
                        "body": "Refs #261",
                    }
                return {
                    "state": "OPEN",
                    "headRefOid": head_ac1_init,
                    "mergeStateStatus": "DIRTY",
                    "mergeable": "CONFLICTING",
                    "reviews": [],
                    "statusCheckRollup": [{"name": "test-ci", "conclusion": "SUCCESS"}],
                    "body": "Refs #261",
                }
            gh_state_d062.pr_views[2610] = pr_view_2610

            if os.path.exists(mock_record_d062):
                os.remove(mock_record_d062)
            gh_blocked_labels.clear()

            exit_ac1 = main(["261", "--role", "devops"], cwd=d062_local,
                            run_dir=os.path.join(tmpdir, "d062_run_ac1"))
            assert exit_ac1 == 0, f"Expected exit 0 for AC1, got {exit_ac1}"

            records_ac1 = get_d062_records()
            assert len(records_ac1) >= 1
            author_rec = records_ac1[0]
            assert author_rec["role"] == "devops"
            assert author_rec["merge_in_progress"] is True, "Merge was not in progress when author ran!"
            assert "conflicts with main" in author_rec["task"]
            assert "state:blocked" not in gh_blocked_labels, f"Issue was blocked: {gh_blocked_labels}"
            print("test_d062_ac1_conflicting_pr_gives_author_round_with_merge_in_progress: OK")

            # AC1 negative check: author modifies a file outside role paths during conflict resolution -> deliver blocks
            subprocess.run(["git", "-C", d062_local, "switch", "-q", "-C", "ops/D-062-neg-branch", "HEAD~1"], check=True)
            with open(os.path.join(d062_local, "common.txt"), "w") as f:
                f.write("conflict branch content\n")
            subprocess.run(["git", "-C", d062_local, "commit", "-q", "-a", "-m", "conflict"], check=True)
            subprocess.run(["git", "-C", d062_local, "push", "-q", "-u", "origin", "ops/D-062-neg-branch"], check=True)
            subprocess.run(["git", "-C", d062_local, "merge", "--no-edit", "origin/main"], check=False)
            assert os.path.exists(os.path.join(d062_local, ".git", "MERGE_HEAD"))
            with open(os.path.join(d062_local, "common.txt"), "w") as f:
                f.write("resolved\n")
            with open(os.path.join(d062_local, "core", "unauthorized.rs"), "w") as f:
                f.write("bad code\n")
            neg_blocked = False
            try:
                deliver("devops", 261, "Title", "ops/D-062-neg-branch", "PR-Summary: bad", ["common.txt"], 2610, cwd=d062_local)
            except Stop as exc:
                neg_blocked = True
                assert exc.status == 2
                assert "changed paths outside its role" in str(exc)
            assert neg_blocked, "deliver did not block when author touched file outside role during conflict"
            subprocess.run(["git", "-C", d062_local, "switch", "-q", "main"], check=True)
            print("test_d062_ac1_unauthorized_path_during_conflict_blocks: OK")

            # --- AC2: Open PR behind main that merges cleanly ---
            # 1. On origin main, add main_clean.txt
            with open(os.path.join(d062_origin, "main_clean.txt"), "w") as f:
                f.write("clean main\n")
            subprocess.run(["git", "-C", d062_origin, "add", "main_clean.txt"], check=True)
            subprocess.run(["git", "-C", d062_origin, "commit", "-q", "-m", "Main clean commit"], check=True)

            # 2. PR branch adds branch_clean.txt from previous main commit
            subprocess.run(["git", "-C", d062_origin, "branch", "-f", "ops/D-062-ac2-branch", "HEAD~1"], check=True)
            subprocess.run(["git", "-C", d062_local, "fetch", "-q", "origin"], check=True)
            subprocess.run(["git", "-C", d062_local, "switch", "-q", "-C", "ops/D-062-ac2-branch", "origin/ops/D-062-ac2-branch"], check=True)
            with open(os.path.join(d062_local, "branch_clean.txt"), "w") as f:
                f.write("clean branch\n")
            subprocess.run(["git", "-C", d062_local, "add", "branch_clean.txt"], check=True)
            subprocess.run(["git", "-C", d062_local, "commit", "-q", "-m", "Branch clean commit"], check=True)
            subprocess.run(["git", "-C", d062_local, "push", "-q", "-u", "origin", "ops/D-062-ac2-branch"], check=True)
            head_ac2_init = subprocess.run(["git", "-C", d062_origin, "rev-parse", "ops/D-062-ac2-branch"],
                                           capture_output=True, text=True, check=True).stdout.strip()
            subprocess.run(["git", "-C", d062_local, "switch", "-q", "main"], check=True)

            gh_state_d062.issue_info[262] = {
                "title": "D-062 AC2 issue",
                "state": "OPEN",
                "labels": [{"name": "state:ready"}, {"name": "area:tooling"}],
                "body": "### Touches\n- `branch_clean.txt`\n",
            }
            gh_state_d062.open_prs = [{
                "number": 2620,
                "headRefName": "ops/D-062-ac2-branch",
                "headRefOid": head_ac2_init,
                "author": {"login": "nextoomio-devops[bot]"},
                "body": "Refs #262\n\nClean merge PR",
            }]

            def pr_view_2620(num):
                curr_head = subprocess.run(["git", "-C", d062_origin, "rev-parse", "ops/D-062-ac2-branch"],
                                           capture_output=True, text=True).stdout.strip()
                if curr_head != head_ac2_init:
                    return {
                        "state": "MERGED",
                        "headRefOid": curr_head,
                        "mergeStateStatus": "CLEAN",
                        "mergeable": "MERGEABLE",
                        "reviews": [{"author": {"login": "nextoomio-reviewer"}, "commit": {"oid": curr_head},
                                    "state": "APPROVED", "body": "Completes-Story: yes"}],
                        "statusCheckRollup": [{"name": "test-ci", "conclusion": "SUCCESS"}],
                        "body": "Refs #262",
                    }
                return {
                    "state": "OPEN",
                    "headRefOid": head_ac2_init,
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                    "reviews": [],
                    "statusCheckRollup": [{"name": "test-ci", "conclusion": "SUCCESS"}],
                    "body": "Refs #262",
                }
            gh_state_d062.pr_views[2620] = pr_view_2620

            if os.path.exists(mock_record_d062):
                os.remove(mock_record_d062)

            exit_ac2 = main(["262", "--role", "devops"], cwd=d062_local,
                            run_dir=os.path.join(tmpdir, "d062_run_ac2"))
            assert exit_ac2 == 0, f"Expected exit 0 for AC2, got {exit_ac2}"

            records_ac2 = get_d062_records()
            roles_ac2 = [r["role"] for r in records_ac2]
            assert "devops" not in roles_ac2 and "developer" not in roles_ac2, f"Author was started in AC2: {roles_ac2}"
            assert "reviewer" in roles_ac2
            head_ac2_final = subprocess.run(["git", "-C", d062_origin, "rev-parse", "ops/D-062-ac2-branch"],
                                            capture_output=True, text=True, check=True).stdout.strip()
            assert head_ac2_final != head_ac2_init, "Merge commit was not pushed!"
            print("test_d062_ac2_clean_merge_pushes_without_author_round: OK")

            # AC2 sub-scenario: PR with CHANGES_REQUESTED behind main that merges cleanly -> author round runs
            with open(os.path.join(d062_origin, "main_clean2.txt"), "w") as f:
                f.write("clean main 2\n")
            subprocess.run(["git", "-C", d062_origin, "add", "main_clean2.txt"], check=True)
            subprocess.run(["git", "-C", d062_origin, "commit", "-q", "-m", "Main clean 2 commit"], check=True)

            subprocess.run(["git", "-C", d062_origin, "branch", "-f", "ops/D-062-ac2-cr-branch", "HEAD~1"], check=True)
            subprocess.run(["git", "-C", d062_local, "fetch", "-q", "origin"], check=True)
            subprocess.run(["git", "-C", d062_local, "switch", "-q", "-C", "ops/D-062-ac2-cr-branch", "origin/ops/D-062-ac2-cr-branch"], check=True)
            with open(os.path.join(d062_local, "branch_clean2.txt"), "w") as f:
                f.write("clean branch 2\n")
            subprocess.run(["git", "-C", d062_local, "add", "branch_clean2.txt"], check=True)
            subprocess.run(["git", "-C", d062_local, "commit", "-q", "-m", "Branch clean 2 commit"], check=True)
            subprocess.run(["git", "-C", d062_local, "push", "-q", "-u", "origin", "ops/D-062-ac2-cr-branch"], check=True)
            head_ac2_cr_init = subprocess.run(["git", "-C", d062_origin, "rev-parse", "ops/D-062-ac2-cr-branch"],
                                              capture_output=True, text=True, check=True).stdout.strip()
            subprocess.run(["git", "-C", d062_local, "switch", "-q", "main"], check=True)

            gh_state_d062.issue_info[264] = {
                "title": "D-062 AC2 changes requested issue",
                "state": "OPEN",
                "labels": [{"name": "state:ready"}, {"name": "area:tooling"}],
                "body": "### Touches\n- `branch_clean2.txt`\n",
            }
            gh_state_d062.open_prs = [{
                "number": 2640,
                "headRefName": "ops/D-062-ac2-cr-branch",
                "headRefOid": head_ac2_cr_init,
                "author": {"login": "nextoomio-devops[bot]"},
                "body": "Refs #264\n\nClean merge with CHANGES_REQUESTED",
            }]

            def pr_view_2640(num):
                curr_head = subprocess.run(["git", "-C", d062_origin, "rev-parse", "ops/D-062-ac2-cr-branch"],
                                           capture_output=True, text=True).stdout.strip()
                if curr_head != head_ac2_cr_init:
                    revs = subprocess.run(["git", "-C", d062_origin, "rev-list", f"{head_ac2_cr_init}..{curr_head}"],
                                          capture_output=True, text=True).stdout.splitlines()
                    if len(revs) >= 2:
                        return {
                            "state": "MERGED",
                            "headRefOid": curr_head,
                            "mergeStateStatus": "CLEAN",
                            "mergeable": "MERGEABLE",
                            "reviews": [{"author": {"login": "nextoomio-reviewer"}, "commit": {"oid": curr_head},
                                        "state": "APPROVED", "body": "Completes-Story: yes"}],
                            "statusCheckRollup": [{"name": "test-ci", "conclusion": "SUCCESS"}],
                            "body": "Refs #264",
                        }
                    else:
                        return {
                            "state": "OPEN",
                            "headRefOid": curr_head,
                            "mergeStateStatus": "CLEAN",
                            "mergeable": "MERGEABLE",
                            "reviews": [{"author": {"login": "nextoomio-reviewer"}, "commit": {"oid": head_ac2_cr_init},
                                        "state": "CHANGES_REQUESTED", "body": "Please address review"}],
                            "statusCheckRollup": [{"name": "test-ci", "conclusion": "SUCCESS"}],
                            "body": "Refs #264",
                        }
                return {
                    "state": "OPEN",
                    "headRefOid": head_ac2_cr_init,
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                    "reviews": [{"author": {"login": "nextoomio-reviewer"}, "commit": {"oid": head_ac2_cr_init},
                                "state": "CHANGES_REQUESTED", "body": "Please address review"}],
                    "statusCheckRollup": [{"name": "test-ci", "conclusion": "SUCCESS"}],
                    "body": "Refs #264",
                }
            gh_state_d062.pr_views[2640] = pr_view_2640

            if os.path.exists(mock_record_d062):
                os.remove(mock_record_d062)

            exit_ac2_cr = main(["264", "--role", "devops"], cwd=d062_local,
                               run_dir=os.path.join(tmpdir, "d062_run_ac2_cr"))
            assert exit_ac2_cr == 0, f"Expected exit 0 for AC2 CR, got {exit_ac2_cr}"
            records_ac2_cr = get_d062_records()
            assert len(records_ac2_cr) >= 2, f"Expected author then reviewer, got {records_ac2_cr}"
            assert records_ac2_cr[0]["role"] == "devops", f"Author was not run first on clean merge with CHANGES_REQUESTED: {records_ac2_cr}"
            assert "the review requested changes" in records_ac2_cr[0]["task"]
            assert records_ac2_cr[1]["role"] == "reviewer"
            print("test_d062_ac2_clean_merge_with_changes_requested_runs_author: OK")

            # --- AC3: Approved PR that conflicts with main ---
            with open(os.path.join(d062_origin, "common.txt"), "w") as f:
                f.write("main conflict for ac3\n")
            subprocess.run(["git", "-C", d062_origin, "commit", "-q", "-a", "-m", "Main commit for ac3"], check=True)

            subprocess.run(["git", "-C", d062_origin, "branch", "-f", "ops/D-062-ac3-branch", "HEAD~1"], check=True)
            subprocess.run(["git", "-C", d062_local, "fetch", "-q", "origin"], check=True)
            subprocess.run(["git", "-C", d062_local, "switch", "-q", "-C", "ops/D-062-ac3-branch", "origin/ops/D-062-ac3-branch"], check=True)
            with open(os.path.join(d062_local, "common.txt"), "w") as f:
                f.write("branch conflict for ac3\n")
            subprocess.run(["git", "-C", d062_local, "commit", "-q", "-a", "-m", "Branch commit for ac3"], check=True)
            subprocess.run(["git", "-C", d062_local, "push", "-q", "-u", "origin", "ops/D-062-ac3-branch"], check=True)
            head_ac3_init = subprocess.run(["git", "-C", d062_origin, "rev-parse", "ops/D-062-ac3-branch"],
                                           capture_output=True, text=True, check=True).stdout.strip()
            subprocess.run(["git", "-C", d062_local, "switch", "-q", "main"], check=True)

            gh_state_d062.issue_info[263] = {
                "title": "D-062 AC3 issue",
                "state": "OPEN",
                "labels": [{"name": "state:ready"}, {"name": "area:tooling"}],
                "body": "### Touches\n- `common.txt`\n",
            }
            gh_state_d062.open_prs = [{
                "number": 2630,
                "headRefName": "ops/D-062-ac3-branch",
                "headRefOid": head_ac3_init,
                "author": {"login": "nextoomio-devops[bot]"},
                "body": "Refs #263\n\nApproved conflicting PR",
            }]

            def pr_view_2630(num):
                curr_head = subprocess.run(["git", "-C", d062_origin, "rev-parse", "ops/D-062-ac3-branch"],
                                           capture_output=True, text=True).stdout.strip()
                if curr_head != head_ac3_init:
                    return {
                        "state": "MERGED",
                        "headRefOid": curr_head,
                        "mergeStateStatus": "CLEAN",
                        "mergeable": "MERGEABLE",
                        "reviews": [{"author": {"login": "nextoomio-reviewer"}, "commit": {"oid": curr_head},
                                    "state": "APPROVED", "body": "Completes-Story: yes"}],
                        "statusCheckRollup": [{"name": "test-ci", "conclusion": "SUCCESS"}],
                        "body": "Refs #263",
                    }
                return {
                    "state": "OPEN",
                    "headRefOid": head_ac3_init,
                    "mergeStateStatus": "DIRTY",
                    "mergeable": "CONFLICTING",
                    "reviews": [{"author": {"login": "nextoomio-reviewer"}, "commit": {"oid": head_ac3_init},
                                "state": "APPROVED", "body": "Completes-Story: yes"}],
                    "statusCheckRollup": [{"name": "test-ci", "conclusion": "SUCCESS"}],
                    "body": "Refs #263",
                }
            gh_state_d062.pr_views[2630] = pr_view_2630

            if os.path.exists(mock_record_d062):
                os.remove(mock_record_d062)

            exit_ac3 = main(["263", "--role", "devops"], cwd=d062_local,
                            run_dir=os.path.join(tmpdir, "d062_run_ac3"))
            assert exit_ac3 == 0, f"Expected exit 0 for AC3, got {exit_ac3}"

            records_ac3 = get_d062_records()
            assert len(records_ac3) >= 2
            assert records_ac3[0]["role"] == "devops"
            assert records_ac3[0]["merge_in_progress"] is True
            assert records_ac3[1]["role"] == "reviewer", f"Reviewer not started after author resolution: {records_ac3}"
            print("test_d062_ac3_approved_conflicting_pr_author_resolves_and_new_head_reviewed: OK")

            # AC3 sub-scenario: Approved PR reported DIRTY by GitHub, but try_merge_main is UP_TO_DATE -> Stop(2)
            gh_state_d062.issue_info[265] = {
                "title": "D-062 AC3 up-to-date issue",
                "state": "OPEN",
                "labels": [{"name": "state:ready"}, {"name": "area:tooling"}],
                "body": "### Touches\n- `common.txt`\n",
            }
            subprocess.run(["git", "-C", d062_local, "switch", "-q", "-C", "ops/D-062-ac3-uptodate", "origin/main"], check=True)
            subprocess.run(["git", "-C", d062_local, "push", "-q", "-u", "origin", "ops/D-062-ac3-uptodate"], check=True)
            head_ac3_uptodate = subprocess.run(["git", "-C", d062_origin, "rev-parse", "ops/D-062-ac3-uptodate"],
                                               capture_output=True, text=True, check=True).stdout.strip()
            subprocess.run(["git", "-C", d062_local, "switch", "-q", "main"], check=True)

            gh_state_d062.open_prs = [{
                "number": 2650,
                "headRefName": "ops/D-062-ac3-uptodate",
                "headRefOid": head_ac3_uptodate,
                "author": {"login": "nextoomio-devops[bot]"},
                "body": "Refs #265\n\nApproved but reported dirty",
            }]
            gh_state_d062.pr_views[2650] = lambda num: {
                "state": "OPEN",
                "headRefOid": head_ac3_uptodate,
                "mergeStateStatus": "DIRTY",
                "mergeable": "CONFLICTING",
                "reviews": [{"author": {"login": "nextoomio-reviewer"}, "commit": {"oid": head_ac3_uptodate},
                            "state": "APPROVED", "body": "Completes-Story: yes"}],
                "statusCheckRollup": [{"name": "test-ci", "conclusion": "SUCCESS"}],
                "body": "Refs #265",
            }
            old_wait_merge = wait_for_merge
            try:
                globals()["wait_for_merge"] = lambda num, timeout=900: False
                exit_ac3_uptodate = main(["265", "--role", "devops"], cwd=d062_local,
                                         run_dir=os.path.join(tmpdir, "d062_run_ac3_uptodate"))
                assert exit_ac3_uptodate == 2, f"Expected exit 2 on UP_TO_DATE dirty PR, got {exit_ac3_uptodate}"
            finally:
                globals()["wait_for_merge"] = old_wait_merge
            print("test_d062_ac3_post_approval_dirty_uptodate_stops_with_exit_2: OK")

            # AC3 sub-scenario: Conflict rounds bounded by MAX_ROUNDS
            subprocess.run(["git", "-C", d062_origin, "branch", "-f", "ops/D-062-conflict-limit", "HEAD~1"], check=True)
            subprocess.run(["git", "-C", d062_local, "fetch", "-q", "origin"], check=True)
            subprocess.run(["git", "-C", d062_local, "switch", "-q", "-C", "ops/D-062-conflict-limit", "origin/ops/D-062-conflict-limit"], check=True)
            with open(os.path.join(d062_local, "common.txt"), "w") as f:
                f.write("conflict content for limit test\n")
            subprocess.run(["git", "-C", d062_local, "commit", "-q", "-a", "-m", "conflict for limit"], check=True)
            subprocess.run(["git", "-C", d062_local, "push", "-q", "-u", "origin", "ops/D-062-conflict-limit"], check=True)
            head_limit_init = subprocess.run(["git", "-C", d062_origin, "rev-parse", "ops/D-062-conflict-limit"],
                                             capture_output=True, text=True, check=True).stdout.strip()
            subprocess.run(["git", "-C", d062_local, "switch", "-q", "main"], check=True)

            gh_state_d062.issue_info[266] = {
                "title": "D-062 AC3 conflict limit issue",
                "state": "OPEN",
                "labels": [{"name": "state:ready"}, {"name": "area:tooling"}],
                "body": "### Touches\n- `common.txt`\n",
            }
            gh_state_d062.open_prs = [{
                "number": 2660,
                "headRefName": "ops/D-062-conflict-limit",
                "headRefOid": head_limit_init,
                "author": {"login": "nextoomio-devops[bot]"},
                "body": "Refs #266\n\nConflicting PR for limit test",
            }]
            gh_state_d062.pr_views[2660] = lambda num: {
                "state": "OPEN",
                "headRefOid": head_limit_init,
                "mergeStateStatus": "DIRTY",
                "mergeable": "CONFLICTING",
                "reviews": [],
                "statusCheckRollup": [{"name": "test-ci", "conclusion": "SUCCESS"}],
                "body": "Refs #266",
            }

            gh_blocked_labels.clear()
            old_max_rounds = MAX_ROUNDS
            try:
                globals()["MAX_ROUNDS"] = 0
                exit_conflict_limit = main(["266", "--role", "devops"], cwd=d062_local,
                                           run_dir=os.path.join(tmpdir, "d062_run_conflict_limit"))
                assert exit_conflict_limit == 2, f"Expected exit 2 when conflict rounds exceeded, got {exit_conflict_limit}"
                assert "state:blocked" in gh_blocked_labels, f"Expected issue blocked: {gh_blocked_labels}"
            finally:
                globals()["MAX_ROUNDS"] = old_max_rounds
            print("test_d062_ac3_conflict_rounds_bounded_by_max_rounds: OK")

            # --- AC6: Offline coverage ---
            assert gh is mock_gh_d062
            assert TOOLING is d062_tooling
            print("test_d062_ac6_offline_coverage: OK")
        finally:
            gh = old_gh_d062
            TOOLING = old_tooling_d062
            REQUIRED_CHECKS = old_required_checks_d062
            os.environ.pop("D062_RECORD_FILE", None)

        # D-063: GitHub writes are recorded rather than sent to the network.
        old_agent, old_gh_json, old_gh = agent, gh_json, gh
        try:
            sha = subprocess.check_output(["git", "-C", repo_dir, "rev-parse", "main"], text=True).strip()
            writes = []
            comments = []
            prompts = []
            def qm_agent(role, task, log, cwd=None):
                prompts.append(task)
            def qm_json(args, **kwargs):
                return {"comments": comments} if "comments" in args else {"body": "- [ ] **AC1**"}
            def qm_write(args, **kwargs):
                writes.append(args)
            agent, gh_json, gh = qm_agent, qm_json, qm_write

            def comment(body, timestamp, login="nextoomio-qm[bot]"):
                return {"author": {"login": login}, "body": body, "createdAt": timestamp}

            old = "2000-01-01T00:00:00Z"
            future = "2999-01-01T00:00:00Z"
            cases = [
                ([comment(f"QM-Verdict: PASS\nVerified-Commit: {sha}", old)], "no QM comment"),
                ([comment("QM-Verdict: PASS", future)], "no Verified-Commit"),
                ([comment("QM-Verdict: PASS\nVerified-Commit: " + "0" * 40, future)], "does not match"),
                ([comment(f"QM-Verdict: FAIL\nVerified-Commit: {sha}", future)], "did not give"),
                ([comment(f"QM-Verdict: PASS\nVerified-Commit: {sha}", future, "other[bot]")], "no QM comment"),
            ]
            for fixture, message in cases:
                comments[:] = fixture
                writes.clear()
                try:
                    run_qm_and_close(252, 1, "unused", tmpdir, cwd=repo_dir)
                    raise AssertionError("invalid verdict was accepted")
                except Stop as exc:
                    assert exc.status == 2 and message in str(exc), str(exc)
                assert not writes, writes
            comments[:] = [comment(f"QM-Verdict: PASS\nVerified-Commit: {sha}", future)]
            writes.clear()
            assert run_qm_and_close(252, 1, "unused", tmpdir, cwd=repo_dir) == 0
            assert [args[:2] for args in writes] == [["issue", "edit"], ["issue", "close"]]
            assert sha in prompts[-1] and "Verified-Commit:" in prompts[-1]
            print("test_d063_fresh_verdict_and_commit_gate: OK")
        finally:
            agent, gh_json, gh = old_agent, old_gh_json, old_gh

    print("OK: all self-tests passed.")
    return 0


def main(argv=None, cwd=None, run_dir=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("issue", type=int, nargs="?", help="issue number to run through the pipeline")
    parser.add_argument("--role", choices=["developer", "devops"], help="author role for the issue")
    parser.add_argument("--test", action="store_true", help="run self-tests and exit")
    args = parser.parse_args(argv)

    if args.test:
        return run_self_tests()

    if args.issue is None:
        parser.error("the following arguments are required: issue")

    issue = args.issue
    log = None

    try:
        global REQUIRED_CHECKS
        REQUIRED_CHECKS = get_required_checks()
        required_checks = REQUIRED_CHECKS
        info = gh_json(["issue", "view", str(issue), "--json", "title,state,labels,body"])
        labels = {l["name"] for l in info["labels"]}
        if info["state"] != "OPEN" or not ({"state:ready", "state:implemented"} & labels):
            raise Stop(1, f"issue #{issue} is not open with state:ready or state:implemented")
        role = args.role or ("devops" if labels & DEVOPS_AREAS or "type:enabler" in labels else "developer")

        if run(["git", "status", "--porcelain"], cwd=cwd).stdout.strip():
            raise Stop(1, "the working tree is not clean")
        run(["git", "fetch", "--prune", "origin"], cwd=cwd)
        run(["git", "switch", "main"], cwd=cwd)
        run(["git", "pull", "--ff-only"], cwd=cwd)

        if run_dir is None:
            run_dir = os.path.expanduser(
                f"~/.config/nextoomio/runs/{issue}-{datetime.datetime.now():%Y%m%d-%H%M%S}")
        os.makedirs(run_dir, mode=0o700, exist_ok=True)
        log = os.path.join(run_dir, "agents.log")
        say(f"issue #{issue} \"{info['title']}\" -> {role}; log {log}")

        global TOOLING
        if TOOLING is None:
            TOOLING = pin_tooling(run_dir, root=cwd or ROOT)
            say(f"tooling pinned to {TOOLING.tooling_dir}")

        if "state:implemented" in labels:
            merged = find_merged_pr(issue, role)
            if not merged:
                raise Stop(1, f"issue #{issue} has state:implemented but no merged pull request that references it")
            return run_qm_and_close(issue, merged["number"], log, run_dir, cwd=cwd)

        # "Touches" entries outside the role's default paths, for the guard where it runs.
        touches = re.findall(r"^- `([^`]+)`", (info["body"].split("### Touches", 1) + [""])[1].split("###", 1)[0], re.M)

        ident = re.match(r"[A-Za-z0-9.-]+", info["title"]).group(0)
        slug = re.sub(r"[^a-z0-9]+", "-", info["title"][len(ident):].lower()).strip("-")[:32].strip("-")
        branch = f"{'ops' if role == 'devops' else 'dev'}/{ident}-{slug}"
        existing = find_pr(issue, role)
        number = existing["number"] if existing else None
        branch = choose_branch(branch, pr=existing, cwd=cwd)

        rounds = 0
        conflict_rounds = 0
        skip_author = False
        verdict = None
        review_body = ""

        if existing:
            head = pr_state(number)["headRefOid"]
            (verdict, review_body) = latest_review(number, head)
            rounds = len([r for r in pr_state(number).get("reviews", [])
                          if is_app(r.get("author", {}).get("login"), "reviewer") and r.get("state") == "CHANGES_REQUESTED"])
            run(["git", "switch", branch], cwd=cwd)
            merge_status = try_merge_main(branch, role, cwd=cwd)
            if merge_status == "CONFLICT":
                conflict_rounds += 1
                if conflict_rounds > MAX_ROUNDS:
                    block(issue, f"pull request #{number} conflicted with main {conflict_rounds} times; "
                                 "the Architect decides whether the story or the approach is wrong.")
                task = conflict_author_task(number, issue)
                skip_author = False
                verdict = None
            elif merge_status == "CLEAN_MERGE":
                if verdict == "CHANGES_REQUESTED":
                    task = round_author_task(number, issue, "the review requested changes")
                    skip_author = False
                    verdict = None
                else:
                    skip_author = True
                    verdict = None
                    run(["git", "switch", "main"], cwd=cwd, check=False)
            else:
                if verdict == "APPROVED":
                    run(["git", "switch", "main"], cwd=cwd, check=False)
                    say(f"pull request #{number} at {head[:7]}")
                    say(f"pull request #{number}: APPROVED")
                elif verdict == "CHANGES_REQUESTED":
                    task = round_author_task(number, issue, "the review requested changes")
                else:
                    skip_author = True
                    run(["git", "switch", "main"], cwd=cwd, check=False)
        else:
            task = (f"Work on issue {issue} ({info['title']}) in repository {REPO}. Follow your role definition.")

        while True:
            if verdict != "APPROVED":
                while True:
                    if not skip_author:
                        output = agent(role, task, log, extra_paths=touches, cwd=cwd)
                        delivered = deliver(role, issue, info["title"], branch, output, touches, number, cwd=cwd)
                        number = delivered or number
                        run(["git", "switch", "main"], cwd=cwd, check=False)
                        if not delivered and number:
                            block(issue, f"the {role} changed nothing in response to the review or CI result on "
                                         f"pull request #{number}.")
                        if not number:
                            current = gh_json(["issue", "view", str(issue), "--json", "labels"])
                            if "state:blocked" in {l["name"] for l in current["labels"]}:
                                raise Stop(2, f"issue #{issue} was blocked by the {role}; see its comment")
                            block(issue, f"the {role} finished without changing anything.")
                    skip_author = False

                    head = pr_state(number)["headRefOid"]
                    say(f"pull request #{number} at {head[:7]}")
                    gh(["pr", "merge", str(number), "--auto", "--merge"], role=role, check=False)

                    if not wait_for_ci(number, required_checks=required_checks, issue=issue):
                        verdict, why, review_body = "CHANGES_REQUESTED", "CI failed on the head commit", ""
                    else:
                        agent("reviewer", f"Review pull request {number} (issue {issue}) at its current head commit. "
                                          "Follow your role definition.", log, cwd=cwd)
                        head = pr_state(number)["headRefOid"]
                        (verdict, review_body), why = latest_review(number, head), "the review requested changes"
                        if verdict is None:
                            block(issue, f"the Reviewer submitted no verdict on pull request #{number} at {head[:7]}.")
                    say(f"pull request #{number}: {verdict}")
                    if verdict == "APPROVED":
                        break
                    rounds += 1
                    if rounds > MAX_ROUNDS:
                        block(issue, f"pull request #{number} was not approved after {MAX_ROUNDS} rounds of changes; "
                                     "the Architect decides whether the story or the approach is wrong.")
                    task = round_author_task(number, issue, why)
                    run(["git", "switch", branch], cwd=cwd)

            # Auto-merge enabled before the approval did not fire on its own in the
            # first runs; asking again merges at once when the rules are met, and
            # only then.
            gh(["pr", "merge", str(number), "--auto", "--merge"], role=role, check=False)
            if wait_for_merge(number):
                break
            state = pr_state(number)
            if (str(state.get("mergeStateStatus") or "").upper() == "DIRTY"
                    or str(state.get("mergeable") or "").upper() == "CONFLICTING"):
                run(["git", "switch", branch], cwd=cwd)
                merge_status = try_merge_main(branch, role, cwd=cwd)
                if merge_status == "CONFLICT":
                    conflict_rounds += 1
                    if conflict_rounds > MAX_ROUNDS:
                        block(issue, f"pull request #{number} conflicted with main {conflict_rounds} times; "
                                     "the Architect decides whether the story or the approach is wrong.")
                    task = conflict_author_task(number, issue)
                    skip_author = False
                    verdict = None
                elif merge_status == "CLEAN_MERGE":
                    skip_author = True
                    verdict = None
                    run(["git", "switch", "main"], cwd=cwd, check=False)
                elif merge_status == "UP_TO_DATE":
                    raise Stop(2, f"pull request #{number} is reported dirty/conflicting by GitHub, "
                                  f"but is already up to date with origin/main")
                else:
                    raise Stop(2, f"unexpected merge status {merge_status} for pull request #{number}")
                continue
            raise Stop(2, f"pull request #{number} is approved but GitHub has not merged it; check the rules on main")
        say(f"pull request #{number} merged")
        run(["git", "pull", "--ff-only"], cwd=cwd)
        if not re.search(r"^Completes-Story:\s*yes\s*$", review_body, re.M | re.I):
            raise Stop(2, f"pull request #{number} merged, but the review did not state 'Completes-Story: yes'; "
                          f"issue #{issue} stays ready for a further pull request")
        gh(["issue", "edit", str(issue), "--remove-label", "state:ready", "--add-label", "state:implemented"],
           role="architect")

        return run_qm_and_close(issue, number, log, run_dir, cwd=cwd)
    except Stop as stop:
        say(str(stop))
        return stop.status
    except KeyboardInterrupt:
        say("interrupted")
        return 1
    except Exception as exc:
        say(f"unexpected error: {exc}")
        if log:
            try:
                with open(log, "a", encoding="utf-8") as f:
                    traceback.print_exc(file=f)
            except Exception:
                pass
        return 1
    finally:
        cleanup_checkout(cwd=cwd)


if __name__ == "__main__":
    sys.exit(main())
