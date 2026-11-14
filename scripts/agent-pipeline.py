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
        f.write(f"\n===== {role} @ {datetime.datetime.now().isoformat()} =====\n{out.stdout}\n")
    say(f"{role}: finished (exit {out.returncode})")
    return out.stdout or ""


def bot_identity(role):
    bot = f"nextoomio-{role}[bot]"
    bot_id = gh(["api", f"users/nextoomio-{role}%5Bbot%5D", "--jq", ".id"]).strip()
    return bot, f"{bot_id}+{bot}@users.noreply.github.com"


def changed_paths():
    out = run(["git", "status", "--porcelain", "-uall"]).stdout
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
        r"^(\s*(?:[-*]\s+)?(?:\*\*)?Image(?:\s+version)?(?:\s+and\s+commit)?(?::\*\*|\*\*:|:)\s*)(.*)$",
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
            new_lines.append(f"{m.group(1)}{new_value}")
        else:
            new_lines.append(line)
    return "".join(new_lines), count


def replace_commit_markers(paths, commit_hash, root=ROOT):
    """Replace {{COMMIT}} in 'Image version and commit' field of changed evidence files (D-039).

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


def deliver(role, issue, title, branch, output, extra_paths, number):
    """Commit and push the author's changes as the role; open the pull request if there is none.

    Returns the pull request number, or None when the author changed nothing.
    """
    paths = changed_paths()
    if not paths:
        return None
    refused = outside_role_paths(role, paths, extra_paths)
    if refused:
        run(["git", "stash", "push", "-u", "-m", f"pipeline: refused changes for issue {issue}"])
        run(["git", "switch", "main"])
        block(issue, f"the {role} changed paths outside its role, so nothing was committed: "
                     + ", ".join(f"`{p}`" for p in refused) + ". The changes are kept in a local stash.")
    found = re.search(r"^PR-Title:\s*(.+)$", output, re.M)
    pr_title = found.group(1).strip() if found else title
    summary = output.split("PR-Summary:", 1)[1].strip() if "PR-Summary:" in output else \
        "The author gave no summary; see the diff."
    bot, email = bot_identity(role)
    env = dict(os.environ, GIT_AUTHOR_NAME=bot, GIT_COMMITTER_NAME=bot,
               GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_EMAIL=email)
    run(["git", "add", "-A"], env=env)
    run(["git", "commit", "-q", "-m", f"{pr_title}\n\nRefs #{issue}"], env=env)

    # D-039: Replace {{COMMIT}} marker in changed evidence files with the full change commit hash.
    change_commit = run(["git", "rev-parse", "HEAD"]).stdout.strip()
    updated_evidence = replace_commit_markers(paths, change_commit)
    if updated_evidence:
        for p in updated_evidence:
            run(["git", "add", p], env=env)
        run(["git", "commit", "-q", "-m", f"Record change commit in evidence\n\nRefs #{issue}"], env=env)

    helper = "!f() { echo username=x-access-token; echo \"password=$NEXTOOMIO_PUSH_TOKEN\"; }; f"
    run(["git", "-c", "credential.helper=", "-c", f"credential.helper={helper}",
         "push", "-q", "-u", "origin", branch], env=dict(env, NEXTOOMIO_PUSH_TOKEN=token(role)))
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


def pr_state(number):
    return gh_json(["pr", "view", str(number), "--json",
                    "state,headRefOid,reviewDecision,mergeStateStatus,reviews,statusCheckRollup"])


def wait_for_ci(number, timeout=3600):
    """Wait until every required check has reported on the head commit. True if all passed."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        checks = pr_state(number)["statusCheckRollup"] or []
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


def run_self_tests():
    """Run self-tests for pinned tooling (D-040) and commit marker replacement (D-039)."""
    print("Running agent-pipeline self-tests...")
    with tempfile.TemporaryDirectory() as tmpdir:
        # Test 1: pin_tooling copies all required files and marks them executable (D-040, AC1)
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

        # Test 2: AC2 verification on a change branch with broken scripts (D-040, AC2)
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
        global TOOLING
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
        print("test_agent_run_pinned_agents: OK")

        # Test 4: AC1 verification of replace_commit_markers and deliver commit replacement (D-039)
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
        assert "Verify that {{COMMIT}} in prose is preserved" in replaced_text, "Prose marker was rewritten!"
        assert "The {{COMMIT}} marker in prose remains" in replaced_text, "Prose marker in observed result rewritten!"

        # In-repo git workflow test for replace_commit_markers
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

        # Run replace_commit_markers (called by deliver)
        updated = replace_commit_markers(paths, change_commit, root=d039_repo)
        assert updated == [ev_rel], f"Expected [{ev_rel}], got: {updated}"

        # Verify file content
        with open(ev_file, "r") as f:
            final_content = f.read()
        assert f"- **Image version and commit:** none (repository only); commit {change_commit}" in final_content
        assert "Verify that {{COMMIT}} in prose is preserved" in final_content
        assert "The {{COMMIT}} marker in prose remains" in final_content

        # Create follow-up commit as deliver() does
        for p in updated:
            subprocess.run(["git", "-C", d039_repo, "add", p], check=True)
        subprocess.run(["git", "-C", d039_repo, "commit", "-q", "-m", "Record change commit in evidence\n\nRefs #99"], check=True)

        follow_up_commit = subprocess.run(["git", "-C", d039_repo, "rev-parse", "HEAD"],
                                          capture_output=True, text=True, check=True).stdout.strip()
        assert follow_up_commit != change_commit

        # Verify follow-up commit touched only the evidence file
        diff_files = subprocess.run(["git", "-C", d039_repo, "diff-tree", "--no-commit-id", "--name-only", "-r", follow_up_commit],
                                    capture_output=True, text=True, check=True).stdout.strip().splitlines()
        assert diff_files == [ev_rel], f"Follow-up commit changed unexpected files: {diff_files}"

        print("test_d039_commit_marker_replacement: OK")

    print("OK: all self-tests passed.")
    return 0


def main(argv=None):
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

    info = gh_json(["issue", "view", str(issue), "--json", "title,state,labels,body"])
    labels = {l["name"] for l in info["labels"]}
    if info["state"] != "OPEN" or "state:ready" not in labels:
        raise Stop(1, f"issue #{issue} is not open with state:ready")
    role = args.role or ("devops" if labels & DEVOPS_AREAS or "type:enabler" in labels else "developer")

    if run(["git", "status", "--porcelain"]).stdout.strip():
        raise Stop(1, "the working tree is not clean")
    run(["git", "switch", "main"])
    run(["git", "pull", "--ff-only"])

    run_dir = os.path.expanduser(
        f"~/.config/nextoomio/runs/{issue}-{datetime.datetime.now():%Y%m%d-%H%M%S}")
    os.makedirs(run_dir, mode=0o700)
    log = os.path.join(run_dir, "agents.log")
    say(f"issue #{issue} \"{info['title']}\" -> {role}; log {log}")

    global TOOLING
    TOOLING = pin_tooling(run_dir)
    say(f"tooling pinned to {TOOLING.tooling_dir}")

    # "Touches" entries outside the role's default paths, for the guard where it runs.
    touches = re.findall(r"^- `([^`]+)`", (info["body"].split("### Touches", 1) + [""])[1].split("###", 1)[0], re.M)

    ident = re.match(r"[A-Za-z0-9.-]+", info["title"]).group(0)
    slug = re.sub(r"[^a-z0-9]+", "-", info["title"][len(ident):].lower()).strip("-")[:32].strip("-")
    branch = f"{'ops' if role == 'devops' else 'dev'}/{ident}-{slug}"
    existing = find_pr(issue, role)
    number = existing["number"] if existing else None
    if run(["git", "rev-parse", "--verify", "-q", f"origin/{branch}"], check=False).returncode == 0:
        if run(["git", "switch", branch], check=False).returncode != 0:
            run(["git", "switch", "-c", branch, f"origin/{branch}"])
        run(["git", "pull", "--ff-only"], check=False)
    else:
        run(["git", "switch", "-c", branch])

    task = (f"Work on issue {issue} ({info['title']}) in repository {REPO}. Follow your role definition.")
    rounds = 0
    while True:
        output = agent(role, task, log, extra_paths=touches)
        delivered = deliver(role, issue, info["title"], branch, output, touches, number)
        number = delivered or number
        run(["git", "switch", "main"], check=False)
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

        if not wait_for_ci(number):
            verdict, why, review_body = "CHANGES_REQUESTED", "CI failed on the head commit", ""
        else:
            agent("reviewer", f"Review pull request {number} (issue {issue}) at its current head commit. "
                              "Follow your role definition.", log)
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
        run(["git", "switch", branch])

    # Auto-merge enabled before the approval did not fire on its own in the
    # first runs; asking again merges at once when the rules are met, and
    # only then.
    gh(["pr", "merge", str(number), "--auto", "--merge"], role=role, check=False)
    if not wait_for_merge(number):
        raise Stop(2, f"pull request #{number} is approved but GitHub has not merged it; check the rules on main")
    say(f"pull request #{number} merged")
    run(["git", "pull", "--ff-only"])
    if not re.search(r"^Completes-Story:\s*yes\s*$", review_body, re.M | re.I):
        raise Stop(2, f"pull request #{number} merged, but the review did not state 'Completes-Story: yes'; "
                      f"issue #{issue} stays ready for a further pull request")
    gh(["issue", "edit", str(issue), "--remove-label", "state:ready", "--add-label", "state:implemented"],
       role="architect")

    agent("qm", f"Verify issue {issue}, which now has state:implemented; its pull request {number} is merged. "
                "Follow your role definition.", log)
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


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except Stop as stop:
        say(str(stop))
        sys.exit(stop.status)
