#!/usr/bin/env python3
"""Run one ready issue through the agent team without a person in the loop.

Usage: scripts/agent-pipeline.py <issue number> [--role developer|devops]

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
import subprocess
import sys
import time

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


def is_app(login, role):
    """GitHub reports an App as 'nextoomio-x', 'app/nextoomio-x', or 'nextoomio-x[bot]'."""
    name = (login or "").removeprefix("app/").removesuffix("[bot]")
    return name == f"nextoomio-{role}"


class Stop(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def run(cmd, env=None, check=True, capture=True, log=None):
    out = subprocess.run(cmd, cwd=ROOT, env=env, text=True,
                         stdout=subprocess.PIPE if capture else None,
                         stderr=subprocess.STDOUT if capture else None)
    if log and out.stdout:
        with open(log, "a") as f:
            f.write(out.stdout)
    if check and out.returncode != 0:
        raise Stop(1, f"command failed ({out.returncode}): {' '.join(cmd[:4])} ...\n{(out.stdout or '')[-800:]}")
    return out


def token(role):
    return run([os.path.join(HERE, "agent-token.sh"), role]).stdout.strip()


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


def agent(role, task, log, extra_paths=None):
    """Start one role headlessly and wait for it."""
    env = dict(os.environ)
    if extra_paths:
        env["NEXTOOMIO_EXTRA_PATHS"] = ":".join(extra_paths)
    cmd = [os.path.join(HERE, "agent-run.sh"), role, "-p", task]
    if role in ("reviewer", "qm", "architect"):
        cmd += ["--permission-mode", "auto"]
    say(f"{role}: started")
    try:
        out = subprocess.run(cmd, cwd=ROOT, env=env, text=True, stdin=subprocess.DEVNULL,
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


def outside_role_paths(role, paths, extra_paths):
    """Paths the role may not edit, judged by the same guard the Claude Code roles run under."""
    env = dict(os.environ, NEXTOOMIO_EXTRA_PATHS=":".join(extra_paths or []))
    guard = os.path.join(ROOT, ".claude", "hooks", "role-guard.py")
    refused = []
    for path in paths:
        event = json.dumps({"tool_name": "Write", "cwd": ROOT,
                            "tool_input": {"file_path": os.path.join(ROOT, path)}})
        out = subprocess.run([sys.executable, guard, role], input=event, text=True,
                             capture_output=True, env=env)
        if out.stdout.strip():
            refused.append(path)
    return refused


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("issue", type=int)
    parser.add_argument("--role", choices=["developer", "devops"])
    args = parser.parse_args()
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
        main()
    except Stop as stop:
        say(str(stop))
        sys.exit(stop.status)
