# Agent team

Document version: **0.4 (adopted on merge)**  
Date: **2026-11-14**  
Status: **Adopted by the project owner by merging this document. Section 7 states what is in place and what is not. Statements about GitHub and Claude Code behavior marked "verify at setup" were read from documentation or are expected behavior; section 9.2 records what has been tried.**  
Related rules: [AGENTS.md](../../AGENTS.md), [backlog conventions](../backlog/README.md), [code-quality rules](code-quality.md).

Version 0.2 records the architect decisions for OQ-01 to OQ-16, adds the review findings RF-01 to RF-10, defines the content of the code-quality rules, and lists the work items that follow from approval.

Version 0.3 replaces the two shared Apps of OQ-01 and OQ-12 with one App per role, as chosen by the project owner, and records what was created and checked on 2026-11-14 (section 9.1).

Version 0.4 turns the proposal into the team document: it is renamed from `agent-team-proposal.md`, the code-quality rules move to their own file, the agent definitions, the guard, and the session scripts exist, and section 7 records the state of each change.

## 1. Goal

Split project work across independent agents, one per role, instead of doing all work in one conversation. Each agent has its own context, its own tools and permissions, and the model that suits its task.

Reasons recorded by the project owner:

- One long conversation accumulates context and lowers the quality of later work.
- Roles need separation of duties: the agent that writes a change must not be the one that approves it.
- Each role needs different tools and benefits from a different model.

## 2. Principles

1. **Artifacts, not chat history, carry state.** Agents never inherit each other's conversation. The shared state is the repository (specification, roadmap, architecture, rules) and GitHub (issues, labels, pull requests, reviews). Every agent starts from one input artifact and finishes with one output artifact that can be checked.
2. **One owner per artifact type.** Requirements and architecture documents, backlog issues, defects, and the agent rules themselves are owned by the Architect; code changes by the Developer; CI, build, and bench by DevOps; reviews by the Reviewer; verification verdicts by QM.
3. **The project owner approves and merges.** Approving and merging a pull request is the only mandatory human gate and stays with the project owner. No agent merges. Section 9, OQ-01, makes this technically enforced instead of a convention.
4. **Rules are referenced, not copied.** Every agent definition points to `AGENTS.md`, the backlog conventions, and the code-quality rules. Agent definitions add only role-specific responsibilities, inputs, outputs, and limits, so the shared rules cannot drift apart.
5. **No agent claims what it has not shown.** The existing rule (no "passed", "verified", or "supported" without evidence) applies to every agent's output, including review verdicts.
6. **Language.** All artifacts (issues, pull requests, review comments, documents, commit messages) are English. Reports addressed directly to the project owner are Slovak, as `AGENTS.md` requires.
7. **Every output names its role and model.** Pull-request descriptions, reviews, verdicts, and findings end with a line `Role: <role> · Model: <model id>`, so that the separation of duties can be audited afterwards.

## 3. Decided by the project owner (2026-11-14)

| # | Decision |
| --- | --- |
| D-A | Agents are started **manually** by the project owner for a specific issue or pull request. No coordinator agent and no label-triggered automation for now. |
| D-B | **Every agent reports findings; only the Architect files defects.** The Architect deduplicates, assigns the resolving story, and creates the `D-<nnn>` issue. |
| D-C | **Several Developer agents may work in parallel**, each in its own git worktree and branch. This requires changing the worktree rule in `AGENTS.md` (section 7). |
| D-D | Roles: Architect, Developer, PR Reviewer / Code Quality, QM / Verifier, DevOps. |

## 4. Roles

Paths not listed under "May edit" are read-only for that role. `SK/` is read-only for every role.

### 4.1 Architect

- **Responsibility:** From the specification, design the approach and components (architecture decisions `AD-*`, open decisions `OD-*`); write features and stories; triage findings into defects; mark stories ready; keep `SPECIFICATION.md`, `ROADMAP.md`, and `ARCHITECTURE.md` consistent and requirement coverage complete; maintain the agent rules.
- **Input:** The maintained documents, a requested scope (for example "stories for P1-F6"), or findings reported by other agents.
- **Output:** A documentation pull request on an `arch/` branch, new or edited backlog issues, defect issues.
- **May edit:** Markdown documents in the repository root and under `docs/` except `docs/evidence/`; `AGENTS.md`; `.claude/agents/`; issues and labels.
- **May not:** Edit source code, build configuration, CI, or recorded evidence; approve or merge pull requests.
- **Model:** `fable`, effort high.

### 4.2 Developer

- **Responsibility:** Implement exactly one story or defect fix.
- **Input:** One issue labelled `state:ready`.
- **Output:** One `dev/` branch and one pull request referencing the issue with `Refs #<n>`, with CI passing and the Definition of Done items 1–5 of the backlog conventions addressed.
- **May edit:** `core/`, `matter/`, `webui/`, `docs/api/`, tests, and other files the story names under "Touches"; `README.md` and `CONTRIBUTING.md` where the change affects them; the rows of the component and license register (`ARCHITECTURE.md` section 10) for a component the story names, to pin its exact version and record its license (RF-03).
- **May not:** Change `SPECIFICATION.md`, `ROADMAP.md`, any other part of `ARCHITECTURE.md`, acceptance criteria, CI workflows, or the agent rules; introduce a third-party component the story does not name. If a story cannot be delivered as written, it stops and follows OQ-09. It does not review or merge pull requests and does not close issues.
- **Model:** `sonnet`, effort medium; `fable`, effort high, for stories labelled `risk:high`.

### 4.3 PR Reviewer / Code Quality

- **Responsibility:** Review every pull request against the linked issue, the maintained documents, and the code-quality rules (section 6), including whether the change stays inside the author role's paths.
- **Input:** One pull request, its diff, its linked issue, and the CI result.
- **Output:** One review: approve, or request changes with concrete, located findings, and the statement whether this pull request completes the story (RF-04). Problems outside the pull request are reported as findings (OQ-10).
- **May edit:** Nothing. It writes the review and finding issues.
- **May not:** Push commits, edit files, merge, or review a pull request produced by the same role and model in the same session.
- **Model:** `opus`, effort high. This always differs from the author's model under the assignments in this section.

### 4.4 QM / Verifier

- **Responsibility:** Decide whether a story meets its Definition of Done. Check the evidence recorded under `docs/evidence/<phase>/<story-id>.md` against every acceptance criterion, and look for claims in code, documents, or CI that go beyond the evidence.
- **Input:** One issue with `state:implemented`, its merged pull requests, and its evidence file.
- **Output:** A verdict comment on the issue (criteria met or not, criterion by criterion) and findings (OQ-10). It recommends closing; the project owner closes.
- **May edit:** Nothing. It writes issue comments and finding issues.
- **May not:** Edit files, record evidence itself, tick acceptance criteria, or close issues.
- **Model:** `sonnet`, effort high.

### 4.5 DevOps

- **Responsibility:** CI workflows, kas and Yocto build, image and update-file production, deploy tooling, bench operation, and recording bench evidence.
- **Input:** One enabler or story issue in its area labelled `state:ready`, a story with `state:implemented` that needs hardware evidence, or a failed build.
- **Output:** A pull request on an `ops/` branch with build or CI changes, or with an evidence file stating the observed result.
- **May edit:** `.github/`, `kas/`, `meta-nextoomio/`, `scripts/`, `Makefile`, `.gitignore`, and `docs/evidence/`; the register rows of `ARCHITECTURE.md` section 10 under the same condition as the Developer.
- **May not:** Edit original application code, the maintained requirement documents, or the agent rules. Bench limits are in OQ-14.
- **Model:** `sonnet`, effort medium; it may delegate reading long build logs to `haiku`.
- **Concurrency:** One DevOps agent at a time, because the bench and the build cache are single resources.

## 5. Workflow

```
SPECIFICATION ─► Architect ─► stories (ready) ─► Developer ─► PR ─► CI ─► Reviewer ─► owner approves and merges
                    ▲                                                                  │
                    │                                                     DevOps: bench evidence (PR)
                    │                                                                  │
                    └──────────── findings ◄─── Reviewer / QM / DevOps ◄─── QM verdict ┘
```

1. The Architect writes or updates the stories for a feature and labels them `state:ready` (OQ-06).
2. The project owner starts a Developer agent for one ready story. Parallel stories run in separate worktrees (OQ-07).
3. The Developer opens a pull request. CI runs.
4. The project owner starts the Reviewer for the pull request. Requested changes go back to the Developer (a new Developer run on the same branch and worktree). After two rounds of requested changes without approval, the pull request goes to the Architect to decide whether the story or the approach is wrong.
5. The project owner approves and merges. When the Reviewer stated that the pull request completes the story, the owner sets `state:implemented`.
6. DevOps records hardware evidence where the story requires it, as a pull request that goes through steps 3–5.
7. The project owner starts QM. A positive verdict lets the owner tick the criteria and close the story as completed.
8. Any agent that finds a problem outside its task reports a finding; the Architect files the defect.

Documentation pull requests from the Architect follow steps 3–5 with the Reviewer applying the documentation checklist (section 6.3).

Pull requests never use a closing keyword under this model: an issue is closed only by the owner after the QM verdict (RF-05).

## 6. Code-quality rules

[code-quality.md](code-quality.md) holds the rules the Reviewer applies (OQ-05, OQ-13): the mechanically enforced rules `CQ-M*` that belong in CI, the review rules for code `CQ-R*`, and the documentation checklist `CQ-D*`. The Architect maintains it; changes are reviewed and merged like any other rule change (OQ-11).

## 7. State of the changes

"In place" means the file or setting exists as described. It does not mean that an agent session has used it; section 9.2 records what was tried.

| File or setting | Change | State |
| --- | --- | --- |
| `AGENTS.md` | Worktrees under `.claude/worktrees/` (D-C, OQ-07); `ARCHITECTURE.md` named as a source of truth (defect D-037, #69); the "Agent team" section | In place |
| `docs/backlog/README.md` | Findings and D-B (OQ-10); `state:ready`, `state:blocked`, `risk:high`, and "Touches" (OQ-06, OQ-07); no closing keywords (RF-05); who sets `state:implemented` (RF-04); Definition of Done items 4 and 5 (RF-03) | In place |
| `.claude/agents/` | One definition per role (section 8) | In place |
| `.claude/hooks/role-guard.py` | Path and command guard (OQ-02) | In place |
| `docs/engineering/code-quality.md` | Section 6 | In place |
| `scripts/agent-token.sh`, `scripts/agent-run.sh` | Role identity for a session (OQ-12, section 8) | In place |
| `.gitignore` | `.claude/worktrees/` | In place |
| GitHub labels | `state:ready`, `state:blocked`, `type:finding`, `risk:high` | In place |
| GitHub Apps | One per role (section 9.1) | In place |
| Rule set on `main` and `.github/CODEOWNERS` | OQ-01; extends P0-F8.2 (#85) | Not in place |
| `.github/PULL_REQUEST_TEMPLATE.md` | Role and model line and the issue reference; P0-F8.1 (#84) covers the template | Not in place |
| CI | Role path scope (CQ-M10) and lock files (CQ-M8) | Not in place |
| Bench key | OQ-14 | Not in place |

## 8. Agent definitions and starting a session

Each file under `.claude/agents/<role>.md` (`architect`, `developer`, `reviewer`, `qm`, `devops`) has frontmatter and a short body:

- `name` and a one-line `description`.
- `model` and `effort` as assigned in section 4.
- `disallowedTools`: the Reviewer and QM definitions remove the file-editing tools altogether. Every definition removes the tool that starts subagents, because it is not established that a subagent runs under the role's guard; the DevOps delegation of log reading to `haiku` (section 4.5) is therefore not available until that is checked.
- `hooks`: a `PreToolUse` hook on the editing tools and on the shell that calls `.claude/hooks/role-guard.py` with the role name (OQ-02).
- Body: the role's task and limits in brief, by reference to this document; the instruction to read `AGENTS.md`, the backlog conventions, and this document before acting; the role and model line; and the instruction to report to the project owner in Slovak.

The project owner starts a role with `scripts/agent-run.sh <role>`, adding Claude Code arguments such as `--worktree <name>` for Developer runs. The script gives `gh` and `git` a private configuration that contains only the short-lived token of the role's App, refreshes it while the session runs, sets the commit author to the App's bot identity, and starts `claude --agent <role>`. `scripts/agent-run.sh --check <role>` sets up the identity, prints it, and exits. The script needs the `claude` command-line program to be signed in. Section 9.2 records the sessions that were started this way.

Paths that a story names under "Touches" outside the role's default paths are passed by the owner in `NEXTOOMIO_EXTRA_PATHS` (patterns separated by colons) when starting the session.

## 9. Resolved questions

Decisions are by the Architect unless marked otherwise. They take effect when the project owner approves this document.

| ID | Question | Decision and reason |
| --- | --- | --- |
| OQ-01 | Reviewer identity | **One GitHub App per role, and the owner's approval as the enforced gate.** The Apps are `nextoomio-architect`, `nextoomio-developer`, `nextoomio-reviewer`, `nextoomio-qm`, and `nextoomio-devops` (section 9.1); the choice of one identity per role instead of two shared ones was made by the project owner, so that every pull request, review, and comment shows its role in GitHub itself. The project owner keeps the personal account `nextoo-ai` and no agent uses it. The rule set on `main` requires a pull request, the CI checks, and a code-owner review with `* @nextoo-ai` as the only code owner. Pull requests are then authored by an App, so the owner can approve them, which the shared identity prevents today; no App can satisfy the code-owner requirement, so no agent can merge. The Reviewer's review is the owner's input, not a second required approval. Option (b) is rejected: GitHub's terms allow one person a single free machine account, which does not cover five roles. Option (c) alone leaves "no agent merges" unenforced. **Not yet in place:** the rule set does not exist, so the three Apps with contents write can at present push to `main` and merge. Verify at setup that a code-owner review can be required on this user-owned repository and that the owner can approve App-authored pull requests. **Until agents use the Apps**, all agents act as `nextoo-ai`, the Reviewer submits a comment review with an explicit verdict line, and the merge rule rests on instructions and the local guard only; this interim state is not separation of duties and is recorded as such. |
| OQ-02 | Enforcing write scopes | **Three layers.** (1) The Reviewer and QM definitions have no editing tools. (2) Each definition carries a `PreToolUse` hook that calls one shared guard script: it rejects edits outside the role's paths and rejects `gh pr merge`, pushes to `main`, forced pushes, and issue closing from the shell. Claude Code cannot restrict paths or command prefixes per agent through permission rules, only through such a hook. (3) Because a shell command can still write any file, CI check CQ-M10 fails a pull request that touches paths outside the role named by its branch prefix (`arch/`, `dev/`, `ops/`), and the Reviewer applies CQ-R13. The branch prefix is a declaration by the author; the check catches drift and mistakes, and the owner's approval remains the control against deliberate circumvention. The guard and the agent definitions are themselves Architect-owned paths. |
| OQ-03 | Model and effort per role | As assigned in section 4: Architect `fable` high; Developer `sonnet` medium, `fable` high for `risk:high`; Reviewer `opus` high; QM `sonnet` high; DevOps `sonnet` medium with `haiku` for logs. The Architect sets `risk:high` on stories that touch the session manager, the update engine, `nx-sysd`, authentication, secrets, or the group channel. The Reviewer never runs on the same model as the author of the pull request it reviews. All models come from one vendor, so the independence of a review comes mainly from the separate context and the separate brief; the different model is an additional measure, not the main one. Fallback when a model is unavailable: the next more capable one, never the author's for a review; the role and model line records what actually ran. Definitions use model aliases, not dated identifiers. |
| OQ-04 | Where agents run | **Local sessions for every role.** Cloud sessions have no bench access and would need the App tokens distributed to another environment. Revisit when automatic triggering (D-A) is revisited. |
| OQ-05 | Code-quality rule content | Section 6. Documentation pull requests get their own checklist (6.3). |
| OQ-06 | Ready state | `state:ready` is set only by the Architect when: every dependency is closed as completed, or has `state:implemented` and the story does not need the dependency's hardware result; every acceptance criterion is checkable; "Touches" is filled in; and `risk:high` is set where it applies. `state:blocked` replaces `state:ready`, always with a comment stating the reason and what unblocks it. A Developer or DevOps agent refuses to start on an issue without `state:ready` and removes neither label itself except to set `state:blocked`. |
| OQ-07 | Worktree conventions | **Location:** `.claude/worktrees/<name>` inside the checkout, the location of Claude Code's `--worktree` option, ignored by git; this keeps the `AGENTS.md` rule that nothing lives outside the project directory. **Branches:** `dev/<issue-id>-<slug>`, `ops/<issue-id>-<slug>`, `arch/<slug>`, for example `dev/P0-F4.5-trial-deadline` or `dev/D-031-plural-format`. **Conflicts:** every story lists the paths or modules it changes under "Touches"; stories with overlapping "Touches" are not run in parallel, and a Developer checks the open pull requests for overlap before starting and stops if it finds one. **Cleanup:** the head branch is deleted on merge; the worktree is removed by the owner, or by the next agent run asked to do so, after the pull request is merged or closed, never while it is open. Architect and DevOps work in the main checkout, one at a time each. Verify at setup how `--worktree` names its branch and whether the branch name can be set. |
| OQ-08 | QM and Reviewer overlap | **Keep separate**, as proposed. The Reviewer judges one change before merge; QM judges one story after merge, across all its pull requests and its evidence. QM runs for every story, including those whose evidence is a CI run. |
| OQ-09 | Story that cannot be delivered as written | **Confirmed, with one addition.** The Developer stops, pushes nothing that contradicts the story, sets `state:blocked`, and comments what cannot be delivered, why, and what change to the story or documents would resolve it. The Architect changes the documents or the story in a separate pull request and re-marks the story ready. This also applies when a needed third-party component is not named by the story. |
| OQ-10 | Channel for findings | **Option (b): a finding issue** with label `type:finding`, titled `Finding: <summary>`, stating where, what was expected and by which document, what is there, and how it was established. The Architect triages by converting the same issue into `D-<nnn>` (title, `type:defect`, severity, area, resolving story) or closing it as not planned or duplicate with the reason, so the history stays in one place. Problems inside the pull request under review are review comments, not findings. Suspected security vulnerabilities are never filed publicly: the agent reports them to the project owner only, who uses the private process in `SECURITY.md`. |
| OQ-11 | Who reviews the Architect | **The Reviewer with the documentation checklist, then the owner**, as proposed. Changes to `AGENTS.md`, `.claude/`, and `docs/engineering/` follow the same path. |
| OQ-12 | Credentials per agent | Each role uses the installation token of its own App with the permissions in section 9.1. No App has administration rights, and every App is installed on this repository only. The Architect and the Developer have the same permissions because GitHub permissions do not distinguish paths; their separation is covered by OQ-02. App private keys stay outside the repository on the owner's workstation; a script under `scripts/` mints a short-lived installation token that the owner passes to the session through the environment. Tokens and keys never appear in the repository, logs, or evidence. |
| OQ-13 | Documentation checks in review | Section 6.3. CQ-D1 is automated today, CQ-D3 by P0-F8.3 (#86); the rest is review. |
| OQ-14 | Bench access for DevOps | **A dedicated bench key pair**, added to the key file on the card (AD-23 allows several keys), loaded in an SSH agent for DevOps sessions; the agent process never reads the private key and the owner's personal key is not used. **Without asking**, within an issue that names bench work: read-only diagnostics over SSH, deploying through the update engine, rebooting, and switching the outlet where the story's procedure requires it or to recover an unreachable device; each outlet action is written into the evidence. **Always with the owner's confirmation:** raw writes to block devices or to the `select` partition outside the update engine, changes to the key file or network configuration, a data-partition reset, any action whose failure needs physical access to recover, and any connection to a host other than the bench device and the outlet. One bench session at a time. |
| OQ-15 | Transition | **Confirmed.** PR #70 is merged as it is and does not include this document. The team model arrives in its own pull requests (section 11). Existing issues stay valid; the Architect adds "Touches" and `state:ready` to stories as they come up for work, not in one pass. |
| OQ-16 | Cost and limits | **No monetary budget per role**; the limits are structural: the model and effort assignments of OQ-03, one issue or pull request per run, `fable` only for the Architect and `risk:high` stories, the two-round limit on review cycles (section 5), and the rule that an agent stops and reports instead of widening its task. Revisit with measured usage after Phase 0. |

### 9.1 Identities created on 2026-11-14

The five Apps are private, owned by `nextoo-ai`, without webhooks, and installed on `nextoo-ai/nextoomio` only. The permissions below are those GitHub returned for an installation token of each App on 2026-11-14.

| App | Role | Write | Read |
| --- | --- | --- | --- |
| `nextoomio-architect` | Architect | contents, issues, pull requests | metadata |
| `nextoomio-developer` | Developer | contents, issues, pull requests | metadata |
| `nextoomio-devops` | DevOps | contents, issues, pull requests, workflows | actions, metadata |
| `nextoomio-reviewer` | Reviewer | issues, pull requests | contents, actions, checks, metadata |
| `nextoomio-qm` | QM | issues | contents, pull requests, actions, metadata |

Checked: each token is issued for this repository only; the Reviewer token can read a pull request and is refused (HTTP 403) when writing repository content.

Not checked: opening a pull request or submitting a review as an App, approval of an App-authored pull request by the owner, the code-owner requirement, and any use of the Apps by an agent session.

### 9.2 Checked at adoption (2026-11-14)

| Check | Result |
| --- | --- |
| `role-guard.py` given hook input directly: 7 cases that must pass and 15 that must be denied (paths per role, the `SK/` archive, files outside the repository, merge, push to `main`, forced push, closing an issue, a review by a non-Reviewer, an unknown role) | All as expected |
| `scripts/agent-run.sh --check` for `reviewer` and `developer` | The token sees only this repository; the commit identity is the App's bot; the owner's own `gh` login is unchanged afterwards |
| A non-interactive session started with `scripts/agent-run.sh qm` | Ran on `claude-sonnet-5-5`, the model the definition assigns. `gh` inside the session saw only this repository through the App's token. The tools Write, Edit, and Agent were not available. `gh issue close --help` was denied by the guard |
| A non-interactive session started with `scripts/agent-run.sh developer` | Ran on `claude-sonnet-5-5`. The guard denied an Edit of `SPECIFICATION.md`, a Write to `docs/guard-check.md`, and `git push --dry-run origin HEAD:main`; a Write under `core/` was allowed. The git author identity was `nextoomio-developer[bot]`. The tool Agent was not available |

These runs show that a session started with `--agent` applies the definition's model, removed tools, and hook, for these two roles and Claude Code 2.1.285. The guard matches command text, so it also denies harmless forms such as `--help` of a denied command.

Still to verify, in the first trial run on a small story: that the `effort` setting is applied; that an actual commit, push, pull request, and review from a session appear on GitHub under the App's identity; that the owner can approve an App-authored pull request; how `--worktree` names its branch; and the Architect, Reviewer, and DevOps definitions, which were not started.

## 10. Review findings

Problems found in version 0.1 during architect review, and how version 0.2 resolves them.

| ID | Finding | Resolution |
| --- | --- | --- |
| RF-01 | "No agent merges" was not enforceable: every agent used the owner's own credentials, so an agent's merge was indistinguishable from the owner's. | OQ-01, OQ-12. |
| RF-02 | `main` has no protection today (checked 2026-11-14), so direct pushes are possible for any agent. | P0-F8.2 (#85), extended by OQ-01. |
| RF-03 | Role limits contradicted the Definition of Done: item 5 requires affected documents in the same pull request and item 4 requires the license in the architecture's register, while the Developer was forbidden to touch `ARCHITECTURE.md`. | Developer and DevOps may edit register rows in section 10 for components the story names; all normative content stays with the Architect; items 4 and 5 are reworded accordingly. |
| RF-04 | Nobody was assigned to set `state:implemented`, and a story can span several pull requests. | The Reviewer states whether the pull request completes the story; the owner sets the label when merging. |
| RF-05 | The backlog conventions allow a closing keyword in a pull request that contains the evidence, which would close a story before the QM verdict. | Closing keywords are not used; the owner closes after QM. |
| RF-06 | The public findings channel did not exclude security vulnerabilities. | OQ-10. |
| RF-07 | The agent rules themselves (`AGENTS.md`, `.claude/`, code-quality rules) had no owner, so any agent could have changed its own limits. | Architect-owned, reviewed, and merged by the owner (section 4.1, OQ-11). |
| RF-08 | Review cycles had no end condition. | Two rounds, then the Architect (section 5). |
| RF-09 | The claim that a different model avoids shared blind spots was stronger than can be shown for models of one vendor. | Reworded in OQ-03. |
| RF-10 | P0-F8.2 (#85) lists "required human review by a second person" as out of scope and requires no review at all, which no longer matches the model. | The story is revised with an explaining comment when this document is approved (section 11). |

## 11. Remaining work

Created by the Architect as stories under feature P0-F8 (#71) with the next free identifiers. None of them exists as an issue yet.

| # | Work item | Role | Depends on |
| --- | --- | --- | --- |
| 1 | Trial run of the whole workflow on one small story, recording the checks still open in section 9.2 | All roles | This document merged |
| 2 | Revise P0-F8.2 (#85): rule set with code-owner review, `CODEOWNERS` | DevOps; rule set applied by the owner | This document merged |
| 3 | CI role path-scope check (CQ-M10) and lock-file enforcement (CQ-M8) | DevOps | 1 |
| 4 | Dedicated bench key and bench rules in the evidence template (P0-F8.5, #88) | DevOps; key created by the owner | 1 |
| 5 | Check whether a subagent runs under the role's guard, and enable the DevOps log delegation if it does | Architect | 1 |

Until item 2 is done, the three Apps with contents write can push to `main` and merge; only the instructions and the guard stand against it.

## 12. Out of scope

- Automatic triggering by labels or events (excluded by D-A; may be revisited later).
- A coordinator agent that starts other agents.
- Changes to product scope, requirements, or release allocation.
