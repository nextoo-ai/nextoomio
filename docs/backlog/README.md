# nextoomio backlog conventions

Features, stories, and defects are tracked as [GitHub issues](https://github.com/nextoo-ai/nextoomio/issues), in the open. This file defines how those issues are written and when they may be closed. It is the only backlog content kept in the repository, so there is one source for work items (the issues) and one for the rules (this file).

[SPECIFICATION.md](../../SPECIFICATION.md) defines behavior, [ROADMAP.md](../../ROADMAP.md) defines what each phase delivers, and [ARCHITECTURE.md](../../ARCHITECTURE.md) defines how. An issue never changes those documents by itself; if a story cannot be delivered as written, the documents are changed first through a pull request.

## Where things are

| What | Where |
| --- | --- |
| Phase 0 features and stories | [Milestone "Phase 0 (v0.0.0)"](https://github.com/nextoo-ai/nextoomio/milestone/1), labels `type:feature`, `type:story`, `type:enabler`, `type:spike` |
| Defects | [Label `type:defect`](https://github.com/nextoo-ai/nextoomio/issues?q=is%3Aissue+label%3Atype%3Adefect), with `severity:*` and `area:*` labels |
| Later phases | Milestones `v0.0.1` to `v0.0.5`; their stories are written when the phase before them has produced the results they depend on |
| Verification evidence | `docs/evidence/<phase>/<story-id>.md` in the repository, linked from the issue |

## Identifiers

Identifiers are part of the issue title and never reused.

- **Feature:** `P<phase>-F<n>`, for example `P0-F4`. A feature is a deliverable capability of a phase and the parent issue of its stories.
- **Story:** `P<phase>-F<n>.<m>`, for example `P0-F4.5`. A withdrawn story keeps its identifier and is closed as not planned.
- **Defect:** `D-<nnn>`.
- Requirement IDs (`SYS-03`, `UPD-06`, …), architecture decisions (`AD-22`), open decisions (`OD-04`), and spikes (`S1`) keep the meaning they have in the maintained documents.

## Story types

| Label | Meaning |
| --- | --- |
| `type:story` | Delivers behavior that a person using or operating the device can observe. |
| `type:enabler` | Delivers build, tooling, or structure that stories depend on. |
| `type:spike` | Answers a named question with recorded evidence and a decision. It delivers no feature. |

## Story format

Every story issue states: type, the user-story sentence, references to requirements and architecture, dependencies, numbered acceptance criteria in Given/When/Then form as a task list, the evidence that must be recorded, and what is out of scope. Acceptance criteria are the complete test of the story: a criterion that cannot be checked by a command, a measurement, or a recorded observation is rewritten until it can. A change to the acceptance criteria of an open story is explained in a comment on the issue.

## Defects

A defect is a place where the repository contradicts the specification, the architecture, its own claims, or verifiable upstream facts. A feature that is simply not built yet is not a defect; a stub that claims to be more than it is, is. Everyone who finds a defect files it, whether or not they intend to fix it.

| Label | Meaning |
| --- | --- |
| `severity:blocker` | Prevents a phase exit gate or makes a deliverable unusable. |
| `severity:major` | Wrong behavior, a broken rule of the maintained documents, or a check that reports success while the thing it guards is broken. |
| `severity:minor` | Inaccurate statement, inconsistency, or hygiene issue without functional effect today. |

A defect issue states where it is, what was expected and by which document, what is actually there, the impact, a proposed fix, and how it was established: verified against the file and upstream, read from the files only, or observed on a build or on hardware. It names the story that resolves it. A defect that belongs to a phase without stories is assigned to that phase's milestone and picked up when its stories are written. Security vulnerabilities are reported privately as described in [SECURITY.md](../../SECURITY.md), not as public issues.

## Status

| State | Meaning |
| --- | --- |
| Open | Planned; no work merged. |
| Open with `state:implemented` | Code and documentation merged with CI passing. Not yet shown on hardware. |
| Closed as completed | **Verified:** every acceptance criterion demonstrated on the named hardware, or the defect's fix demonstrated, with evidence recorded. |
| Closed as not planned | Withdrawn or rejected; the reason is in the closing comment. |

Only closed-as-completed issues count toward a phase exit gate. Pull requests therefore reference stories with `Refs #<n>`, not with a closing keyword, unless the pull request also contains the verification evidence. A story is not closed while a defect assigned to it is open.

## Definition of done

A story gets `state:implemented` when:

1. The change is merged through a pull request with all CI jobs passing.
2. Code, comments, documentation, and commit messages are in English.
3. No secret, private key, or credential is in the repository, the image, logs, or recorded evidence.
4. Every new third-party component is pinned to an exact version and its license is recorded in the recipe and in the architecture's component register.
5. Affected documents are updated in the same pull request.

A story is closed as completed when, in addition:

6. Every acceptance criterion has been demonstrated on the hardware named in the story and ticked in the issue.
7. Evidence is recorded under `docs/evidence/<phase>/<story-id>.md`: date, image version and commit, hardware model and revision, the commands or procedure used, and the observed result. A missed target is recorded as missed.
8. Anything that needed physical access to the device is stated explicitly.

## Bench hardware for Phase 0

Raspberry Pi 4 + HiFiBerry Amp+ on wired Ethernet with DHCP, a passive speaker, and a remotely switched power outlet. No microphone. The workstation that deploys images is on the same network.
