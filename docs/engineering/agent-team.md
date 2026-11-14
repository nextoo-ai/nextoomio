# Agent team

Document version: **0.8 (autonomous operation)**  
Date: **2026-11-14**  
Status: **Adopted. The workflow runs without the project owner in the loop (section 3.1). Section 7 states what is in place and what is not; sections 9.2 and 9.3 record what has been tried. Where a decision in section 9 names the project owner as the one who starts agents, approves, merges, or closes, section 3.1 replaces it.**  
Related rules: [AGENTS.md](../../AGENTS.md), [backlog conventions](../backlog/README.md), [code-quality rules](code-quality.md).

Version 0.2 records the architect decisions for OQ-01 to OQ-16, adds the review findings RF-01 to RF-10, defines the content of the code-quality rules, and lists the work items that follow from approval.

Version 0.3 replaces the two shared Apps of OQ-01 and OQ-12 with one App per role, as chosen by the project owner, and records what was created and checked on 2026-11-14 (section 9.1).

Version 0.4 turns the proposal into the team document: it is renamed from `agent-team-proposal.md`, the code-quality rules move to their own file, the agent definitions, the guard, and the session scripts exist, and section 7 records the state of each change.

Version 0.5 records the owner's decisions after the first trial run: no person in the loop, one independent model per role with development in Antigravity, and the Architect as orchestrator (section 3.1). It adds the rule set on `main`, the pipeline, the Antigravity author roles, and the results of the trial (section 9.3).

Version 0.6 records the first two pipeline runs and what they changed: the author roles only change files and run checks, and the pipeline creates the branch, checks the changed paths, commits, pushes, and opens the pull request (section 5 step 1, sections 7 and 8, section 9.3). It also records the bootstrap exception (section 8).

Version 0.7 records the third pipeline run (section 9.3).

Version 0.8 records the first complete pipeline run and ends the bootstrap exception (sections 8, 9.3, and 11).

## 1. Goal

Split project work across independent agents, one per role, instead of doing all work in one conversation. Each agent has its own context, its own tools and permissions, and the model that suits its task.

Reasons recorded by the project owner:

- One long conversation accumulates context and lowers the quality of later work.
- Roles need separation of duties: the agent that writes a change must not be the one that approves it.
- Each role needs different tools and benefits from a different model.

## 2. Principles

1. **Artifacts, not chat history, carry state.** Agents never inherit each other's conversation. The shared state is the repository (specification, roadmap, architecture, rules) and GitHub (issues, labels, pull requests, reviews). Every agent starts from one input artifact and finishes with one output artifact that can be checked.
2. **One owner per artifact type.** Requirements and architecture documents, backlog issues, defects, and the agent rules themselves are owned by the Architect; code changes by the Developer; CI, build, and bench by DevOps; reviews by the Reviewer; verification verdicts by QM.
3. **GitHub merges by rule; nobody merges at discretion.** A pull request reaches `main` only when the rule set is satisfied: the required checks passed and the Reviewer approved the head commit. The author role enables auto-merge and GitHub performs the merge. No agent and no script decides a merge, and the project owner is not a gate (section 3.1).
4. **Rules are referenced, not copied.** Every agent definition points to `AGENTS.md`, the backlog conventions, and the code-quality rules. Agent definitions add only role-specific responsibilities, inputs, outputs, and limits, so the shared rules cannot drift apart.
5. **No agent claims what it has not shown.** The existing rule (no "passed", "verified", or "supported" without evidence) applies to every agent's output, including review verdicts.
6. **Language.** All artifacts (issues, pull requests, review comments, documents, commit messages) are English. Reports addressed directly to the project owner are Slovak, as `AGENTS.md` requires.
7. **Every output names its role and model.** Pull-request descriptions, reviews, verdicts, and findings end with a line `Role: <role> · Model: <model id>`, so that the separation of duties can be audited afterwards.

## 3. Decided by the project owner (2026-11-14)

| # | Decision |
| --- | --- |
| D-A | *Replaced by D-E.* Agents are started **manually** by the project owner for a specific issue or pull request. No coordinator agent and no label-triggered automation for now. |
| D-B | **Every agent reports findings; only the Architect files defects.** The Architect deduplicates, assigns the resolving story, and creates the `D-<nnn>` issue. |
| D-C | **Several Developer agents may work in parallel**, each in its own git worktree and branch. This requires changing the worktree rule in `AGENTS.md` (section 7). |
| D-D | Roles: Architect, Developer, PR Reviewer / Code Quality, QM / Verifier, DevOps. |

### 3.1 Decided by the project owner after the trial run (2026-11-14)

| # | Decision |
| --- | --- |
| D-E | **No person in the loop.** The project owner sees only the result. Starting roles, review cycles, merging, verification, and closing run without the owner. This replaces D-A and the owner's approval and merge. |
| D-F | **Each role runs on an independent model.** Development runs in Antigravity; review runs on Sonnet; the chief Architect is Fable. The assignments for DevOps and QM in section 4 are the Architect's, made so that no role checks work produced by its own model. |
| D-G | **The Architect orchestrates.** It prepares and marks stories ready, starts the pipeline, triages findings, decides blocked stories, and keeps the rules. |

The owner still acts where GitHub requires the account holder: confirming access for changes to App permissions and repository settings.

## 4. Roles

Paths not listed under "May edit" are read-only for that role. `SK/` is read-only for every role.

### 4.1 Architect

- **Responsibility:** From the specification, design the approach and components (architecture decisions `AD-*`, open decisions `OD-*`); write features and stories; triage findings into defects; mark stories ready; keep `SPECIFICATION.md`, `ROADMAP.md`, and `ARCHITECTURE.md` consistent and requirement coverage complete; maintain the agent rules.
- **Input:** The maintained documents, a requested scope (for example "stories for P1-F6"), or findings reported by other agents.
- **Output:** A documentation pull request on an `arch/` branch, new or edited backlog issues, defect issues.
- **May edit:** Markdown documents in the repository root and under `docs/` except `docs/evidence/`; `AGENTS.md`; `.claude/agents/`; issues and labels.
- **May not:** Edit source code, build configuration, CI, or recorded evidence; approve or merge pull requests.
- **Model:** Claude Fable (`fable`), in Claude Code.

### 4.2 Developer

- **Responsibility:** Implement exactly one story or defect fix.
- **Input:** One issue labelled `state:ready`.
- **Output:** One `dev/` branch and one pull request referencing the issue with `Refs #<n>`, with CI passing and the Definition of Done items 1–5 of the backlog conventions addressed.
- **May edit:** `core/`, `matter/`, `webui/`, `docs/api/`, tests, and other files the story names under "Touches"; `README.md` and `CONTRIBUTING.md` where the change affects them; the rows of the component and license register (`ARCHITECTURE.md` section 10) for a component the story names, to pin its exact version and record its license (RF-03).
- **May not:** Change `SPECIFICATION.md`, `ROADMAP.md`, any other part of `ARCHITECTURE.md`, acceptance criteria, CI workflows, or the agent rules; introduce a third-party component the story does not name. If a story cannot be delivered as written, it stops and follows OQ-09. It does not review or merge pull requests and does not close issues.
- **Model:** Gemini 3.8 Flash (High) in Antigravity (`agy`). The label `risk:high` is kept as information for the Reviewer and the Architect; it no longer selects another model.

### 4.3 PR Reviewer / Code Quality

- **Responsibility:** Review every pull request against the linked issue, the maintained documents, and the code-quality rules (section 6), including whether the change stays inside the author role's paths.
- **Input:** One pull request, its diff, its linked issue, and the CI result.
- **Output:** One review: approve, or request changes with concrete, located findings, and the statement whether this pull request completes the story (RF-04). Problems outside the pull request are reported as findings (OQ-10).
- **May edit:** Nothing. It writes the review and finding issues.
- **May not:** Push commits, edit files, merge, or review a pull request produced by the same role and model in the same session.
- **Model:** Claude Sonnet (`sonnet`), in Claude Code. This always differs from the author's model under the assignments in this section.

### 4.4 QM / Verifier

- **Responsibility:** Decide whether a story meets its Definition of Done. Check the evidence recorded under `docs/evidence/<phase>/<story-id>.md` against every acceptance criterion, and look for claims in code, documents, or CI that go beyond the evidence.
- **Input:** One issue with `state:implemented`, its merged pull requests, and its evidence file.
- **Output:** A verdict comment on the issue (criteria met or not, criterion by criterion) and findings (OQ-10). It ends with `QM-Verdict: PASS` or `QM-Verdict: FAIL`; the pipeline closes the issue on PASS.
- **May edit:** Nothing. It writes issue comments and finding issues.
- **May not:** Edit files, record evidence itself, tick acceptance criteria, or close issues.
- **Model:** Claude Opus (`opus`), in Claude Code. It differs from the models of the roles whose work and evidence it checks.

### 4.5 DevOps

- **Responsibility:** CI workflows, kas and Yocto build, image and update-file production, deploy tooling, bench operation, and recording bench evidence.
- **Input:** One enabler or story issue in its area labelled `state:ready`, a story with `state:implemented` that needs hardware evidence, or a failed build.
- **Output:** A pull request on an `ops/` branch with build or CI changes, or with an evidence file stating the observed result.
- **May edit:** `.github/`, `kas/`, `meta-nextoomio/`, `scripts/`, `Makefile`, `.gitignore`, and `docs/evidence/`; the register rows of `ARCHITECTURE.md` section 10 under the same condition as the Developer.
- **May not:** Edit original application code, the maintained requirement documents, or the agent rules. Bench limits are in OQ-14.
- **Model:** Gemini 3.8 Flash (High) in Antigravity (`agy`).
- **Concurrency:** One DevOps agent at a time, because the bench and the build cache are single resources.

## 5. Workflow

```
SPECIFICATION ─► Architect ─► story (state:ready) ─► pipeline
                    ▲                                   │
                    │        author role ─► PR ─► CI ─► Reviewer ──► GitHub merges by rule
                    │             ▲                        │                  │
                    │             └── changes requested ───┘          state:implemented
                    │                                                         │
                    └────────── findings, blocked stories ◄──── QM verdict ─► closed
```

`scripts/agent-pipeline.py <issue>` runs one issue labelled `state:ready` to its end:

1. The author role (Developer, or DevOps for build, CI, image, tooling, and hardware work) implements the issue in the working tree, on a branch the pipeline created. The pipeline then checks every changed path against the role with the guard's own rules; if any is outside, it commits nothing, keeps the changes in a local stash, and blocks the issue. Otherwise it commits and pushes under the role's identity and opens the pull request with the author's summary and `Refs #<n>`.
2. The pipeline enables auto-merge on the pull request as the author's App. GitHub merges it when the rule set on `main` is satisfied, not before.
3. CI runs; the pipeline waits until all required checks have reported. The Reviewer reviews the head commit and states `Completes-Story: yes` or `Completes-Story: no` in the review. A CI failure or requested changes go back to the author on the same branch. After two such rounds without approval the pipeline labels the issue `state:blocked` with the reason, and the Architect decides whether the story or the approach is wrong.
4. After the merge, if the review said `Completes-Story: yes`, the pipeline sets `state:implemented` and starts QM. If it said `no`, the pipeline stops and leaves the issue ready for the next pull request, which a further run delivers.
5. QM ends its verdict with `QM-Verdict: PASS` or `QM-Verdict: FAIL`. On PASS the pipeline ticks the acceptance criteria and closes the issue as completed. Otherwise the issue stays open with the verdict, for the Architect.
6. Any role that finds a problem outside its task reports a finding; the Architect files the defect.

The pipeline makes no judgment of its own: it starts roles and acts on the review state, the CI result, the merge state, and the verdict line that GitHub and the roles report. Its log is kept outside the repository.

Hardware evidence: a story whose criteria need the bench is implemented and merged the same way; DevOps records the evidence in a further pull request, and QM gives `PASS` only when the evidence covers every criterion.

Documentation pull requests from the Architect are reviewed with the documentation checklist ([code-quality.md](code-quality.md), section 3) and merge under the same rule set.

Pull requests never use a closing keyword: an issue is closed only after the QM verdict (RF-05).

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
| `scripts/agent-token.sh`, `scripts/agent-run.sh` | Role identity for a session (OQ-12, section 8); Developer and DevOps start in Antigravity | In place |
| `.gitignore` | `.claude/worktrees/` | In place |
| GitHub labels | `state:ready`, `state:blocked`, `type:finding`, `risk:high` | In place |
| GitHub Apps | One per role (section 9.1) | In place |
| Rule set on `main` | Changes only through a pull request; the three CI checks; one approval of the head commit by someone other than the last pusher; no forced push or deletion; no bypass actor. Auto-merge and branch deletion on merge are enabled in the repository | In place since 2026-11-14; not yet described in `CONTRIBUTING.md` (P0-F8.2, #85) |
| `scripts/agent-pipeline.py` | The workflow of section 5 | In place; section 9.3 records whether it has run. It takes the "Touches" list from the issue body as extra paths for the guard; that list is written by the Architect and is trusted |
| `.github/PULL_REQUEST_TEMPLATE.md` | Role and model line and the issue reference; P0-F8.1 (#84) covers the template | Not in place |
| CI | Role path scope (CQ-M10) and lock files (CQ-M8) | Not in place. For the Developer and DevOps roles, which run in Antigravity without the guard, the pipeline checks the changed paths before it commits, and the Reviewer applies CQ-R13; a pull request made outside the pipeline has the review only |
| Bench key | OQ-14 | Not in place |

## 8. Agent definitions and starting a session

Each file under `.claude/agents/<role>.md` (`architect`, `developer`, `reviewer`, `qm`, `devops`) has frontmatter and a short body:

- `name` and a one-line `description`.
- `model` and `effort` as assigned in section 4.
- `disallowedTools`: the Reviewer and QM definitions remove the file-editing tools altogether. Every definition removes the tool that starts subagents, because it is not established that a subagent runs under the role's guard; the DevOps delegation of log reading to `haiku` (section 4.5) is therefore not available until that is checked.
- `hooks`: a `PreToolUse` hook on the editing tools and on the shell that calls `.claude/hooks/role-guard.py` with the role name (OQ-02).
- Body: the role's task and limits in brief, by reference to this document; the instruction to read `AGENTS.md`, the backlog conventions, and this document before acting; the role and model line; and the instruction to report to the project owner in Slovak.

A role is started with `scripts/agent-run.sh <role>`, normally by the pipeline. The script gives `gh` and `git` a private configuration that contains only the short-lived token of the role's App, refreshes it while the session runs, removes it afterwards, and sets the commit author to the App's bot identity. `scripts/agent-run.sh --check <role>` sets up the identity, prints it, and exits.

- **Architect, Reviewer, QM** run in Claude Code as `claude --agent <role>`, with the definition's model, removed tools, and guard hook. The command-line program must be signed in.
- **Developer, DevOps** run in Antigravity as `agy -p`, headless. Antigravity has no agent definitions or hooks of ours: the script passes the body of the role's definition together with the task, and the repository's `AGENTS.md` is read by Antigravity itself. **The guard does not run there**, and Antigravity's sandbox does not let `git` write to `.git`, so these roles only change files and run checks; the pipeline makes the branch, commit, push, and pull request for them. Before committing, the pipeline applies the guard's path rules to every changed file, which is a mechanical check of the role's paths for pipeline runs; the Reviewer applies CQ-R13 as well. The `model` field in these two definitions applies only if the role is started in Claude Code instead.

**Bootstrap exception.** `scripts/agent-run.sh`, `scripts/agent-token.sh`, and `scripts/agent-pipeline.py` are DevOps paths, but they are what the DevOps role is started with. Until one pipeline run has delivered a DevOps change from start to end, the Architect may change these three files on an `arch/` branch, through the same review and rule set as any change. The Architect authorised this under D-G. It covered pull requests #154, #158, #159, #160, and #161. **It ended on 2026-11-14** with the first complete pipeline run (section 9.3); these files now change only through DevOps issues.

Paths that a story names under "Touches" outside the role's default paths are passed by the owner in `NEXTOOMIO_EXTRA_PATHS` (patterns separated by colons) when starting the session.

## 9. Resolved questions

Decisions are by the Architect unless marked otherwise. The project owner adopted them by merging version 0.4; section 3.1 changed who acts.

| ID | Question | Decision and reason |
| --- | --- | --- |
| OQ-01 | Reviewer identity | **One GitHub App per role; the rule set on `main` as the gate.** The Apps are `nextoomio-architect`, `nextoomio-developer`, `nextoomio-reviewer`, `nextoomio-qm`, and `nextoomio-devops` (section 9.1); one identity per role was the project owner's choice, so that every pull request, review, and comment shows its role in GitHub itself. No agent uses the owner's account `nextoo-ai`. The rule set requires a pull request, the CI checks, and one approval of the head commit by someone other than the last pusher. An author App cannot approve its own pull request, so the approval can only come from the Reviewer App, and GitHub merges through auto-merge once the rules are met (D-E). The earlier design, a code-owner review by the owner as the gate, was dropped with D-E. A second user account (option b) is not possible for five roles under GitHub's terms, and a review that is only a comment (option c) enforces nothing. Section 9.3 records that the Reviewer App needs contents write for its approval to count. |
| OQ-02 | Enforcing write scopes | **Three layers.** (1) The Reviewer and QM definitions have no editing tools. (2) Each definition carries a `PreToolUse` hook that calls one shared guard script: it rejects edits outside the role's paths and rejects `gh pr merge`, pushes to `main`, forced pushes, and issue closing from the shell. Claude Code cannot restrict paths or command prefixes per agent through permission rules, only through such a hook. (3) Because a shell command can still write any file, CI check CQ-M10 fails a pull request that touches paths outside the role named by its branch prefix (`arch/`, `dev/`, `ops/`), and the Reviewer applies CQ-R13. The branch prefix is a declaration by the author; the check catches drift and mistakes. Since D-E there is no owner approval behind these layers: the controls against a change outside a role's paths are the Reviewer's approval required by the rule set on `main` and, once it exists, CQ-M10. Until then a circumvention that the Reviewer misses is not caught by anything else. The guard and the agent definitions are themselves Architect-owned paths. |
| OQ-03 | Model and effort per role (superseded by D-F and section 4 for the assignments) | As assigned in section 4: Architect `fable` high; Developer `sonnet` medium, `fable` high for `risk:high`; Reviewer `opus` high; QM `sonnet` high; DevOps `sonnet` medium with `haiku` for logs. The Architect sets `risk:high` on stories that touch the session manager, the update engine, `nx-sysd`, authentication, secrets, or the group channel. The Reviewer never runs on the same model as the author of the pull request it reviews. All models come from one vendor, so the independence of a review comes mainly from the separate context and the separate brief; the different model is an additional measure, not the main one. Fallback when a model is unavailable: the next more capable one, never the author's for a review; the role and model line records what actually ran. Definitions use model aliases, not dated identifiers. |
| OQ-04 | Where agents run (still local; started by the pipeline, D-E) | **Local sessions for every role.** Cloud sessions have no bench access and would need the App tokens distributed to another environment. Revisit when automatic triggering (D-A) is revisited. |
| OQ-05 | Code-quality rule content | Section 6. Documentation pull requests get their own checklist (6.3). |
| OQ-06 | Ready state | `state:ready` is set only by the Architect when: every dependency is closed as completed, or has `state:implemented` and the story does not need the dependency's hardware result; every acceptance criterion is checkable; "Touches" is filled in; and `risk:high` is set where it applies. `state:blocked` replaces `state:ready`, always with a comment stating the reason and what unblocks it. A Developer or DevOps agent refuses to start on an issue without `state:ready` and removes neither label itself except to set `state:blocked`. |
| OQ-07 | Worktree conventions | **Location:** `.claude/worktrees/<name>` inside the checkout, the location of Claude Code's `--worktree` option, ignored by git; this keeps the `AGENTS.md` rule that nothing lives outside the project directory. **Branches:** `dev/<issue-id>-<slug>`, `ops/<issue-id>-<slug>`, `arch/<slug>`, for example `dev/P0-F4.5-trial-deadline` or `dev/D-031-plural-format`. **Conflicts:** every story lists the paths or modules it changes under "Touches"; stories with overlapping "Touches" are not run in parallel, and a Developer checks the open pull requests for overlap before starting and stops if it finds one. **Cleanup:** the head branch is deleted on merge; the worktree is removed by the owner, or by the next agent run asked to do so, after the pull request is merged or closed, never while it is open. Architect and DevOps work in the main checkout, one at a time each. Verify at setup how `--worktree` names its branch and whether the branch name can be set. |
| OQ-08 | QM and Reviewer overlap | **Keep separate**, as proposed. The Reviewer judges one change before merge; QM judges one story after merge, across all its pull requests and its evidence. QM runs for every story, including those whose evidence is a CI run. |
| OQ-09 | Story that cannot be delivered as written | **Confirmed, with one addition.** The Developer stops, pushes nothing that contradicts the story, sets `state:blocked`, and comments what cannot be delivered, why, and what change to the story or documents would resolve it. The Architect changes the documents or the story in a separate pull request and re-marks the story ready. This also applies when a needed third-party component is not named by the story. |
| OQ-10 | Channel for findings | **Option (b): a finding issue** with label `type:finding`, titled `Finding: <summary>`, stating where, what was expected and by which document, what is there, and how it was established. The Architect triages by converting the same issue into `D-<nnn>` (title, `type:defect`, severity, area, resolving story) or closing it as not planned or duplicate with the reason, so the history stays in one place. Problems inside the pull request under review are review comments, not findings. Suspected security vulnerabilities are never filed publicly: the agent reports them to the project owner only, who uses the private process in `SECURITY.md`. |
| OQ-11 | Who reviews the Architect | **The Reviewer with the documentation checklist**; the pull request then merges under the rule set on `main` like any other (D-E). Changes to `AGENTS.md`, `.claude/`, and `docs/engineering/` follow the same path. |
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
| `nextoomio-reviewer` | Reviewer | issues, pull requests; contents since the change described below | actions, checks, metadata |
| `nextoomio-qm` | QM | issues | contents, pull requests, actions, metadata |

Checked when the Apps were created: each token is issued for this repository only; the Reviewer token could read a pull request and was refused (HTTP 403) when writing repository content.

Changed later on 2026-11-14: the Reviewer App's contents permission was raised from read to read and write, because its approval did not count toward the required approval otherwise (section 9.3). The refusal above no longer holds.

Not checked at that time: opening a pull request or submitting a review as an App and any use of the Apps by an agent session; section 9.3 records these.

### 9.2 Checked at adoption (2026-11-14)

| Check | Result |
| --- | --- |
| `role-guard.py` given hook input directly: 7 cases that must pass and 15 that must be denied (paths per role, the `SK/` archive, files outside the repository, merge, push to `main`, forced push, closing an issue, a review by a non-Reviewer, an unknown role) | All as expected |
| `scripts/agent-run.sh --check` for `reviewer` and `developer` | The token sees only this repository; the commit identity is the App's bot; the owner's own `gh` login is unchanged afterwards |
| A non-interactive session started with `scripts/agent-run.sh qm` | Ran on `claude-sonnet-5-5`, the model the definition assigns. `gh` inside the session saw only this repository through the App's token. The tools Write, Edit, and Agent were not available. `gh issue close --help` was denied by the guard |
| A non-interactive session started with `scripts/agent-run.sh developer` | Ran on `claude-sonnet-5-5`. The guard denied an Edit of `SPECIFICATION.md`, a Write to `docs/guard-check.md`, and `git push --dry-run origin HEAD:main`; a Write under `core/` was allowed. The git author identity was `nextoomio-developer[bot]`. The tool Agent was not available |

These runs show that a session started with `--agent` applies the definition's model, removed tools, and hook, for these two roles and Claude Code 2.1.285. The guard matches command text, so it also denies harmless forms such as `--help` of a denied command.

Still to verify, in the first trial run on a small story: that the `effort` setting is applied; that an actual commit, push, pull request, and review from a session appear on GitHub under the App's identity; that the owner can approve an App-authored pull request; how `--worktree` names its branch; and the Architect, Reviewer, and DevOps definitions, which were not started.

### 9.3 Trial run on P0-F8.3 (#86), 2026-11-14

The first story went through every role. It ran before D-F, with DevOps on Claude Sonnet, the Reviewer on Claude Opus, and QM on Claude Sonnet, all in Claude Code.

| Step | Observed |
| --- | --- |
| Architect prepares the story | Body edit, `state:ready`, and comment appear on GitHub under `nextoomio-architect[bot]` |
| DevOps implements | Commits, push, and pull request #157 under `nextoomio-devops[bot]`; CI passed |
| Review, round 1 | Changes requested under `nextoomio-reviewer`, with a defect the Reviewer reproduced: section references with the document name in backticks were not checked |
| DevOps, round 2 | A new session with no earlier context fixed both findings from the review on GitHub |
| Review, round 2 | Started without an interactive session; approved the head commit |
| Rule set | With contents read-only, the Reviewer App's approval did **not** count toward the required approval. After its contents permission was raised to read and write, the same approval counted |
| Merge | Auto-merge enabled as the DevOps App; GitHub merged at once because the rules were met |
| QM | Started without an interactive session; verdict on the issue under `nextoomio-qm`; it pointed out an open defect still assigned to the story |
| Close | The Architect detached the half-fixed defect (#69) with a comment, ticked the criteria, and closed the story as completed |

What the trial showed about the model itself:

- **The guard can be bypassed through the shell**, as OQ-02 expected: DevOps changed `ROADMAP.md` with `sed` on a demonstration branch. It disclosed this itself and the change never reached the pull request. The CI path-scope check (CQ-M10) is the missing control.
- **The `effort` setting of a definition was not applied**: the Reviewer's definition says high and the session showed medium. The model setting was applied.
- **The session script left the token file behind** when a terminal tab was closed. It now removes it on hang-up as well.
- **The Reviewer App needs contents write** for its approval to count. It therefore could push to branches; the rule set keeps it from `main`, and it cannot approve its own pull request.

First pipeline run, on D-028 (#60), 2026-11-14: stopped by hand. It found two faults in `scripts/agent-run.sh`, both corrected: started with `--mode accept-edits`, Antigravity denied every shell command, because a headless run cannot ask; and the token refresher kept the caller's output open, so the pipeline waited after the role had ended. A third observation concerns GitHub: auto-merge enabled before the approval did not merge pull request #158 once the rules were met; asking for it again merged at once, and the pipeline now does that. After the corrections, a DevOps run in Antigravity reported the App's commit identity and saw only this repository through `gh`.

Second pipeline run, on #60: the DevOps role ended without output and the pipeline blocked the issue, as designed. Antigravity runs headless with tool permission "request review": a command outside its allow list is denied because nobody can be asked. The owner approved allow rules for `git`, `gh`, `make`, `python3`, `cargo`, `npm`, and `node` in `~/.gemini/antigravity-cli/settings.json`. With them, a file edit worked and commands ran, but `git` could not write to `.git` inside Antigravity's sandbox. Letting `git` leave the sandbox was not done. Instead the author roles no longer use git for writing: the pipeline creates the branch, checks the changed paths, commits, pushes, and opens the pull request (section 5).

Third pipeline run, on #60: the DevOps role again ended without output and without changes; the stored conversation shows `mkdir`, outside the allow list, and a search for `cargo`, which is not installed on the workstation. The allow list was extended with file and text utilities (not `rm`), the Antigravity brief now names the allowed commands and says that any other command ends the run, and #60 was taken back from ready because its criteria need the Rust toolchain.

**First complete pipeline run, on P0-F8.5 (#88), 2026-11-14.** One command, `scripts/agent-pipeline.py 88`, took the issue from `state:ready` to closed as completed in 31 minutes (18:17 to 18:48), with nobody acting in between:

| Step | Observed |
| --- | --- |
| DevOps, Gemini 3.8 Flash (High) in Antigravity | Changed files and ran checks; the pipeline checked the paths, committed as `nextoomio-devops[bot]`, pushed, and opened pull request #162 |
| Reviewer, Claude Sonnet | Requested changes twice, with located findings; approved the third head commit with `Completes-Story: yes` |
| DevOps, rounds 2 and 3 | New runs addressed each review from what is on GitHub |
| Merge | GitHub merged under the rule set after the approval and green checks |
| QM, Claude Opus | Ran the check and its own probes on `main`, gave `QM-Verdict: PASS`, and filed a finding (#163) about two gaps that affect no current file |
| Close | The pipeline ticked the criteria and closed the issue |
| Finding | The Architect converted #163 into defect D-038 with criteria and marked it ready |

This run used the limit of two change rounds; a third request for changes would have blocked the issue.

Observed and not yet resolved:

- After the run the local checkout was left on a detached commit instead of `main`. The pipeline should end on `main`.
- A Reviewer run reported that a local fetch of the pull-request branch was denied and reviewed from the diff alone in that round.
- The Rust toolchain is not installed on the workstation, so no role can run the core checks locally; stories that need them depend on CI.

Not yet shown: a Developer run (application code under `core/`, `matter/`, or `webui/`); a story with hardware evidence; a run that the pipeline blocks after two rounds; parallel runs.

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

Created by the Architect as stories under feature P0-F8 (#71) with the next free identifiers, unless a story is named.

| # | Work item | Role |
| --- | --- | --- |
| 1 | Pipeline faults seen in the first complete run: end on `main`; handle a crash between push and pull-request creation; take the required check names from the rule set instead of a fixed list | DevOps |
| 2 | CI role path-scope check (CQ-M10), the control behind the guard and the only path control for the Antigravity roles; lock-file enforcement (CQ-M8) | DevOps |
| 3 | Describe the rule set in `CONTRIBUTING.md` and record the evidence for P0-F8.2 (#85) | DevOps |
| 4 | Dedicated bench key and bench rules in the evidence template (P0-F8.5, #88); the key is created and placed by the owner | DevOps |
| 5 | Parallel author runs in worktrees under the pipeline (D-C, OQ-07); the pipeline runs one issue at a time | DevOps |
| 6 | Find out why `effort` is not applied and whether a subagent runs under the role's guard | Architect |

## 12. Out of scope

- Starting the pipeline from GitHub events; it is started by the Architect.
- Changes to product scope, requirements, or release allocation.
