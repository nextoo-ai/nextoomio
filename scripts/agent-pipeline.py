#!/usr/bin/env python3
"""Run one ready issue through the agent team without a person in the loop.

Usage: scripts/agent-pipeline.py <issue number> [--role developer|devops]
       scripts/agent-pipeline.py --test

Steps (docs/engineering/agent-team.md, section 5):
  1. The author role implements the issue in the working tree. The pipeline
     checks that the changed paths belong to the role, then commits, pushes,
     and opens the pull request under the role's identity. (The author roles
     run in Antigravity, whose sandbox does not let git write to .git.)
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
from typing import NamedTuple

REPO = os.environ.get("NEXTOOMIO_REPO", "nextoo-ai/nextoomio")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MAX_ROUNDS = 2
DEVOPS_AREAS = {"area:ci", "area:build", "area:image", "area:tooling", "area:hardware"}
REQUIRED_CHECKS = {
    "Traceability, Docs & Yocto Layer Check",
    "Rust Core & License Gate",
    "WebUI & i18n Catalog Gate",
}
AUTHOR_MODEL = os.environ.get("NEXTOOMIO_AGY_MODEL", "gemini-3.8-flash-high") + " (Antigravity)"
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


def deliver(role, issue, title, branch, output, extra_paths, number, cwd=None):
    """Commit and push the author's changes as the role; open the pull request if there is none.

    Returns the pull request number, or None when the author changed nothing.
    """
    paths = changed_paths(cwd=cwd)
    if not paths:
        return None
    refused = outside_role_paths(role, paths, extra_paths, cwd=cwd)
    if refused:
        run(["git", "stash", "push", "-u", "-m", f"pipeline: refused changes for issue {issue}"], cwd=cwd)
        run(["git", "switch", "main"], cwd=cwd)
        block(issue, f"the {role} changed paths outside its role, so nothing was committed: "
                     + ", ".join(f"`{p}`" for p in refused) + ". The changes are kept in a local stash.")
    found = re.search(r"^PR-Title:\s*(.+)$", output, re.M)
    pr_title = found.group(1).strip() if found else title
    summary = output.split("PR-Summary:", 1)[1].strip() if "PR-Summary:" in output else \
        "The author gave no summary; see the diff."
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
        gh(["pr", "comment", str(number), "--body",
            f"{summary}\n\nRole: {role.capitalize()} · Model: {AUTHOR_MODEL}"], role=role)
        return number
    body = (f"Refs #{issue}\n\n{summary}\n\nThe branch, commit, and this pull request were made by the "
            f"pipeline from the author's working tree, after checking the changed paths against the role.\n\n"
            f"Role: {'DevOps' if role == 'devops' else 'Developer'} · Model: {AUTHOR_MODEL}")
    url = gh(["pr", "create", "--base", "main", "--head", branch, "--title", pr_title, "--body", body],
             role=role).strip().splitlines()[-1]
    return int(url.rstrip("/").rsplit("/", 1)[-1])


def find_pr(issue, author_role):
    prs = gh_json(["pr", "list", "--state", "open", "--limit", "50",
                   "--json", "number,body,author,headRefName,headRefOid"])
    pattern = re.compile(rf"\bRefs\s+#{issue}\b")
    for pr in prs:
        if is_app(pr["author"]["login"], author_role) and pattern.search(pr["body"] or ""):
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


def wait_for_ci(number, issue=None, timeout=3600):
    """Wait until every required check has reported on the head commit. True if all passed."""
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
        if any(s in ("FAILURE", "CANCELLED", "TIMED_OUT", "ERROR", "ACTION_REQUIRED") for s in states.values()):
            return False
        if all(states.get(name) == "SUCCESS" for name in REQUIRED_CHECKS):
            return True
        time.sleep(20)
    raise Stop(1, f"the required checks on pull request #{number} did not finish within {timeout // 60} minutes")


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
        run(["git", "switch", "main"], cwd=cwd, check=False)


def run_self_tests():
    """Run self-tests verifying that pinned tooling runs instead of branch tooling (D-040, AC2)."""
    global TOOLING, gh, pr_state, say
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
                    assert "the required checks on pull request #173 did not finish within 1 minutes" in str(exc)
                assert timeout_raised, "wait_for_ci did not time out as before"
                assert len(sleep_calls_timeout) > 0, "Expected sleep calls before timeout"
                assert gh_mutations == [], f"AC2 violated: issue mutations occurred on check timeout: {gh_mutations}"
                print("test_d045_ac2_timeout_raises_stop1_without_blocking: OK")

            finally:
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

            # --- Additional checks: Stop(2) and modified tracked files ---
            # 1. Stop(2) with clean tree:
            if os.path.exists(dirty_file):
                os.remove(dirty_file)
            subprocess.run(["git", "-C", d047_local, "switch", "-q", "-C", "ops/D-047-stop2"], check=True)
            d047_messages.clear()
            def run_stop2():
                try:
                    raise Stop(2, "issue #188 blocked: test block reason")
                except Stop as s:
                    say(str(s))
                    return s.status
                finally:
                    cleanup_checkout(cwd=d047_local)
            status_stop2 = run_stop2()
            assert status_stop2 == 2
            curr_branch_stop2 = subprocess.run(["git", "-C", d047_local, "branch", "--show-current"],
                                               capture_output=True, text=True, check=True).stdout.strip()
            assert curr_branch_stop2 == "main", f"Expected main, got {curr_branch_stop2}"
            print("test_d047_ac1_stop2_clean_tree_switches_to_main: OK")

            # 2. Stop(2) with modified tracked file:
            subprocess.run(["git", "-C", d047_local, "switch", "-q", "-C", "ops/D-047-tracked-mod"], check=True)
            tracked_path = os.path.join(d047_local, "tracked.txt")
            with open(tracked_path, "w") as f:
                f.write("original\n")
            subprocess.run(["git", "-C", d047_local, "add", "tracked.txt"], check=True)
            subprocess.run(["git", "-C", d047_local, "commit", "-q", "-m", "add tracked"], check=True)
            with open(tracked_path, "w") as f:
                f.write("modified uncommitted\n")
            d047_messages.clear()
            status_tracked_mod = run_stop2()
            assert status_tracked_mod == 2
            curr_branch_mod = subprocess.run(["git", "-C", d047_local, "branch", "--show-current"],
                                             capture_output=True, text=True, check=True).stdout.strip()
            assert curr_branch_mod == "ops/D-047-tracked-mod", f"Expected ops/D-047-tracked-mod, got {curr_branch_mod}"
            with open(tracked_path, "r") as f:
                assert f.read() == "modified uncommitted\n"
            assert "ops/D-047-tracked-mod" in d047_messages[-1]
            assert "the working tree is not clean" in d047_messages[-1]
            print("test_d047_ac2_modified_tracked_file_stays_on_branch: OK")
        finally:
            TOOLING = old_tooling_d047
            gh = real_gh_d047
            say = real_say_d047

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

    try:
        info = gh_json(["issue", "view", str(issue), "--json", "title,state,labels,body"])
        labels = {l["name"] for l in info["labels"]}
        if info["state"] != "OPEN" or "state:ready" not in labels:
            raise Stop(1, f"issue #{issue} is not open with state:ready")
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

        # "Touches" entries outside the role's default paths, for the guard where it runs.
        touches = re.findall(r"^- `([^`]+)`", (info["body"].split("### Touches", 1) + [""])[1].split("###", 1)[0], re.M)

        ident = re.match(r"[A-Za-z0-9.-]+", info["title"]).group(0)
        slug = re.sub(r"[^a-z0-9]+", "-", info["title"][len(ident):].lower()).strip("-")[:32].strip("-")
        branch = f"{'ops' if role == 'devops' else 'dev'}/{ident}-{slug}"
        existing = find_pr(issue, role)
        number = existing["number"] if existing else None
        branch = choose_branch(branch, pr=existing, cwd=cwd)

        task = (f"Work on issue {issue} ({info['title']}) in repository {REPO}. Follow your role definition.")
        rounds = 0
        while True:
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
            head = pr_state(number)["headRefOid"]
            say(f"pull request #{number} at {head[:7]}")
            gh(["pr", "merge", str(number), "--auto", "--merge"], role=role, check=False)

            if not wait_for_ci(number, issue=issue):
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
            task = (f"Pull request {number} for issue {issue} needs changes: {why}. Read the review and the CI "
                    f"result with `gh pr view {number} --comments` and `gh pr checks {number}`, and address them. "
                    "Follow your role definition.")
            run(["git", "switch", branch], cwd=cwd)

        # Auto-merge enabled before the approval did not fire on its own in the
        # first runs; asking again merges at once when the rules are met, and
        # only then.
        gh(["pr", "merge", str(number), "--auto", "--merge"], role=role, check=False)
        if not wait_for_merge(number):
            raise Stop(2, f"pull request #{number} is approved but GitHub has not merged it; check the rules on main")
        say(f"pull request #{number} merged")
        run(["git", "pull", "--ff-only"], cwd=cwd)
        if not re.search(r"^Completes-Story:\s*yes\s*$", review_body, re.M | re.I):
            raise Stop(2, f"pull request #{number} merged, but the review did not state 'Completes-Story: yes'; "
                          f"issue #{issue} stays ready for a further pull request")
        gh(["issue", "edit", str(issue), "--remove-label", "state:ready", "--add-label", "state:implemented"],
           role="architect")

        agent("qm", f"Verify issue {issue}, which now has state:implemented; its pull request {number} is merged. "
                    "Follow your role definition.", log, cwd=cwd)
        comments = gh_json(["issue", "view", str(issue), "--json", "comments"])["comments"]
        verdicts = [c["body"] for c in comments if is_app(c["author"]["login"], "qm")]
        passed = bool(verdicts) and re.search(r"^QM-Verdict:\s*PASS\s*$", verdicts[-1], re.M)
        if not passed:
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
    except Stop as stop:
        say(str(stop))
        return stop.status
    except KeyboardInterrupt:
        say("interrupted")
        return 1
    except Exception as exc:
        say(f"unexpected error: {exc}")
        return 1
    finally:
        cleanup_checkout(cwd=cwd)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Stop as stop:
        say(str(stop))
        cleanup_checkout()
        sys.exit(stop.status)
