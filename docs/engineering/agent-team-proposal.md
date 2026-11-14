# Agent team proposal

Document version: **0.2 (architect review complete)**  
Date: **2026-11-14**  
Status: **Reviewed by the Architect; all open questions of version 0.1 are resolved in section 9 and the review findings are in section 10. Awaiting approval by the project owner. Nothing in this document is set up, configured, or in use. Statements about GitHub and Claude Code behavior marked "verify at setup" were read from documentation or are expected behavior and have not been tried in this repository.**  
Related rules: [AGENTS.md](../../AGENTS.md), [backlog conventions](../backlog/README.md).

Version 0.2 records the architect decisions for OQ-01 to OQ-16, adds the review findings RF-01 to RF-10, defines the content of the code-quality rules, and lists the work items that follow from approval.

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

A new document, `docs/engineering/code-quality.md`, holds the rules the Reviewer applies. Its content is decided here (OQ-05, OQ-13); the document is written by the Architect in the introducing pull request and identifiers below are kept.

### 6.1 Mechanically enforced rules

These belong in CI. The Reviewer checks only that the jobs ran on the reviewed commit and passed, and reports a rule that CI claims to enforce but does not as a finding.

| ID | Rule | Enforced today |
| --- | --- | --- |
| CQ-M1 | Rust formatting (`cargo fmt --check`) and Clippy with warnings denied | Yes, `rust-core` job |
| CQ-M2 | Rust tests pass | Yes, `rust-core` job |
| CQ-M3 | Dependency licenses, bans, and sources (`cargo-deny`) | Yes, for `core/` only |
| CQ-M4 | Requirement traceability and Markdown links (`scripts/check_traceability.py`) | Yes |
| CQ-M5 | i18n catalog gate (`scripts/check_i18n.mjs`) | Partly; D-030 records what it misses |
| CQ-M6 | WebUI type-check and build | Yes, `webui` job |
| CQ-M7 | Yocto layer and kas definition check | Yes |
| CQ-M8 | Lock files committed and used (`--locked`, `npm ci`) | No; neither `core/Cargo.lock` nor `webui/package-lock.json` is committed, and CI runs `npm install` without `--locked` builds |
| CQ-M9 | Document versions and cross-references | No; P0-F8.3 (#86) |
| CQ-M10 | Role path scope: a pull request touches only the paths of the role its branch prefix names (OQ-02) | No; new story |

### 6.2 Review rules for code

| ID | Rule |
| --- | --- |
| CQ-R1 | The change does what the linked issue's acceptance criteria require and nothing unrelated. |
| CQ-R2 | No claim beyond the code: comments, documents, test names, CI step names, and commit messages do not say "implemented", "verified", or "supported" for behavior that is absent or stubbed. A stub says it is a stub. |
| CQ-R3 | Requirement IDs are cited only where the behavior exists. |
| CQ-R4 | Tests are derived from the specification scenario or acceptance criterion they name and can fail; they do not restate the implementation. |
| CQ-R5 | Errors that cross the API or MQTT boundary carry stable machine-readable codes, independent of translated text. |
| CQ-R6 | Secrets pass only through a redacting type and never reach logs, diagnostics, error messages, or evidence. |
| CQ-R7 | No user-supplied or network-supplied string reaches a shell or a command line unvalidated; privileged operations go only through `nx-sysd` typed requests (AD-20). |
| CQ-R8 | Playback requests go through the session manager; configured group membership stays separate from the active playback owner. |
| CQ-R9 | No panic, `unwrap`, or `expect` on input from the network, files, or hardware in daemon code; `unsafe` only with a comment stating why it is sound. |
| CQ-R10 | User-facing WebUI text exists only in the English catalog behind stable keys, with interpolation and plural forms instead of concatenation. |
| CQ-R11 | Every new third-party component is named by the story, pinned to an exact version, and recorded with its license in the recipe and the component register; model weights, firmware, and bundled assets are checked separately. |
| CQ-R12 | Nothing in the change depends on correct wall-clock time for a local function (AD-21). |
| CQ-R13 | The change stays inside the author role's paths (section 4). An edit to `ARCHITECTURE.md` from a `dev/` or `ops/` branch is confined to register rows in section 10. |
| CQ-R14 | No abstraction, configuration option, or dependency without a use in this change. |

### 6.3 Documentation checklist

| ID | Rule |
| --- | --- |
| CQ-D1 | Every requirement ID remains covered in the roadmap and the architecture; none is dropped, moved beyond v0.0.5, or renumbered. |
| CQ-D2 | Release assignments agree between specification, roadmap, architecture, and affected issues. |
| CQ-D3 | Document versions and cross-references to other documents and sections are correct, and the version note describes the change. |
| CQ-D4 | Planned, implemented, and hardware-verified are distinguished; no result is stated without recorded evidence. |
| CQ-D5 | A changed or new decision (`AD-*`, `OD-*`) states the alternative and the reason, and affected stories are updated or named. |
| CQ-D6 | License and component statements name the exact version they were checked against. |
| CQ-D7 | `SK/` is unchanged. |
| CQ-D8 | Evidence files contain date, image version and commit, hardware model and revision, procedure, and observed result, with missed targets recorded as missed, and no secret. |

## 7. Required changes to existing rules

These are proposed and not yet made. Section 11 maps them to work items.

| File or setting | Change |
| --- | --- |
| `AGENTS.md` | Allow git worktrees under `.claude/worktrees/` for parallel agents (D-C, OQ-07). Name `ARCHITECTURE.md` as a source of truth (already defect D-037, #69). Reference the team model and the role limits. |
| `docs/backlog/README.md` | Replace "Everyone who finds a defect files it" with D-B and the finding issue (OQ-10). Add `state:ready`, `state:blocked`, `risk:high`, and the "Touches" field (OQ-06, OQ-07). Remove the closing-keyword exception (RF-05). State who sets `state:implemented` (RF-04). Reword Definition of Done items 4 and 5 to match the role limits (RF-03). |
| `.claude/agents/` | One definition per role (section 8). |
| `.claude/hooks/` | The path-scope and command guard used by the agent definitions (OQ-02). |
| `docs/engineering/code-quality.md` | New, section 6. |
| `docs/engineering/agent-team.md` | This document, renamed when adopted, with sections 9 and 10 reduced to the decisions. Agent definitions reference it, not a proposal. |
| `.gitignore` | Add `.claude/worktrees/`. |
| `.github/CODEOWNERS` | `* @nextoo-ai`. |
| `.github/PULL_REQUEST_TEMPLATE.md` | Add the role and model line and the issue reference; P0-F8.1 (#84) already covers the template. |
| GitHub labels | `state:ready`, `state:blocked`, `type:finding`, `risk:high`. |
| GitHub Apps and rule set on `main` | OQ-01 and OQ-12; extends P0-F8.2 (#85). |

## 8. What an agent definition contains

Each file under `.claude/agents/<role>.md` (`architect`, `developer`, `reviewer`, `qm`, `devops`) has frontmatter and a short body:

- `name` and a one-line `description`.
- `model` and `effort` as assigned in section 4.
- `tools`: the Reviewer and QM definitions omit the file-editing tools altogether.
- `hooks`: a `PreToolUse` hook on the editing tools and on the shell that calls the shared guard with the role name (OQ-02).
- Body: responsibility, input, output, and limits by reference to the role's section of the team document; the instruction to read `AGENTS.md`, the backlog conventions, and the code-quality rules before acting; the role and model line; and the instruction to report to the project owner in Slovak.

The project owner starts a role as the main session with `claude --agent <role>`, adding `--worktree <name>` for Developer runs. The Claude Code documentation lists these frontmatter fields and both flags; that the tool restrictions, model, and hooks of a definition apply to a main session started this way is to be verified at setup, and the adoption story records the result.

## 9. Resolved questions

Decisions are by the Architect unless marked otherwise. They take effect when the project owner approves this document.

| ID | Question | Decision and reason |
| --- | --- | --- |
| OQ-01 | Reviewer identity | **Two GitHub Apps, and the owner's approval as the enforced gate.** `nextoomio-author` is used by the Architect, Developer, and DevOps; `nextoomio-reviewer` by the Reviewer and QM. The project owner keeps the personal account `nextoo-ai` and no agent uses it. The rule set on `main` requires a pull request, the CI checks, and a code-owner review with `* @nextoo-ai` as the only code owner. Pull requests are then authored by an App, so the owner can approve them, which the shared identity prevents today; neither App can satisfy the code-owner requirement, so no agent can merge. The Reviewer's review is the owner's input, not a second required approval. Separate identities per author role add nothing that the branch prefix and the role line do not already give. Option (b) is rejected: a second user account cannot use a fine-grained token on a repository owned by another user. Option (c) alone leaves "no agent merges" unenforced. Verify at setup that a code-owner review can be required on this user-owned repository and that App-authored pull requests behave as described. **Until the Apps exist**, all agents act as `nextoo-ai`, the Reviewer submits a comment review with an explicit verdict line, and the merge rule rests on instructions and the local guard only; this interim state is not separation of duties and is recorded as such. |
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
| OQ-12 | Credentials per agent | `nextoomio-author`: contents, pull requests, issues, and workflows write; Actions read. `nextoomio-reviewer`: contents and Actions read; pull requests and issues write. Neither has administration rights. Finer separation between Architect, Developer, and DevOps is not available at token level and is covered by OQ-02. App private keys stay outside the repository on the owner's workstation; a script under `scripts/` mints a short-lived installation token that the owner passes to the session through the environment. Tokens and keys never appear in the repository, logs, or evidence. |
| OQ-13 | Documentation checks in review | Section 6.3. CQ-D1 is automated today, CQ-D3 by P0-F8.3 (#86); the rest is review. |
| OQ-14 | Bench access for DevOps | **A dedicated bench key pair**, added to the key file on the card (AD-23 allows several keys), loaded in an SSH agent for DevOps sessions; the agent process never reads the private key and the owner's personal key is not used. **Without asking**, within an issue that names bench work: read-only diagnostics over SSH, deploying through the update engine, rebooting, and switching the outlet where the story's procedure requires it or to recover an unreachable device; each outlet action is written into the evidence. **Always with the owner's confirmation:** raw writes to block devices or to the `select` partition outside the update engine, changes to the key file or network configuration, a data-partition reset, any action whose failure needs physical access to recover, and any connection to a host other than the bench device and the outlet. One bench session at a time. |
| OQ-15 | Transition | **Confirmed.** PR #70 is merged as it is and does not include this document. The team model arrives in its own pull requests (section 11). Existing issues stay valid; the Architect adds "Touches" and `state:ready` to stories as they come up for work, not in one pass. |
| OQ-16 | Cost and limits | **No monetary budget per role**; the limits are structural: the model and effort assignments of OQ-03, one issue or pull request per run, `fable` only for the Architect and `risk:high` stories, the two-round limit on review cycles (section 5), and the rule that an agent stops and reports instead of widening its task. Revisit with measured usage after Phase 0. |

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

## 11. Work items after approval

None of these exist yet. They are created by the Architect as stories under feature P0-F8 (#71) with the next free identifiers once the project owner approves this document.

| # | Work item | Role | Depends on |
| --- | --- | --- | --- |
| 1 | Adopt the team document, write `code-quality.md`, update `AGENTS.md` and the backlog conventions, create the labels; resolves D-037 (#69) | Architect | PR #70 merged |
| 2 | Agent definitions, guard hook, `.gitignore` entry; record what was verified about `--agent` and `--worktree` | Architect | 1 |
| 3 | Revise P0-F8.2 (#85): rule set with code-owner review, `CODEOWNERS` | DevOps; rule set applied by the owner | 1 |
| 4 | GitHub Apps and the token script | DevOps; Apps created by the owner | 3 |
| 5 | CI role path-scope check (CQ-M10) and lock-file enforcement (CQ-M8) | DevOps | 1 |
| 6 | Dedicated bench key and bench rules in the evidence template (P0-F8.5, #88) | DevOps; key created by the owner | 1 |

Items 3, 4, and 6 need actions only the project owner can take: creating the Apps, applying repository settings, and creating and placing the bench key.

## 12. Out of scope of this proposal

- Automatic triggering by labels or events (excluded by D-A; may be revisited later).
- A coordinator agent that starts other agents.
- Changes to product scope, requirements, or release allocation.
