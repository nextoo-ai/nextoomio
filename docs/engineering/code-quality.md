# Code-quality rules

Document version: **1.1**  
Date: **2026-11-14**  
Related rules: [AGENTS.md](../../AGENTS.md), [backlog conventions](../backlog/README.md), [agent team](agent-team.md).

These are the rules the Reviewer applies to every pull request. They restate nothing that the specification, the architecture, or `AGENTS.md` defines; each rule says what the review checks. Rule identifiers are stable and are cited in review findings.

A review finding names the file and line, the rule, and what would satisfy it. A pull request is approved only when no rule is violated; a rule that does not apply to the change is not mentioned.

The "Enforced today" column describes the CI workflow as of the date above. It is a statement about configuration that was read, not about a run.

## 1. Mechanically enforced rules

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
| CQ-M9 | Document versions and cross-references (`scripts/check_doc_versions.py`) | Yes, since P0-F8.3 (#86) |
| CQ-M10 | Role path scope: a pull request touches only the paths of the role its branch prefix names ([agent team](agent-team.md), OQ-02) | No; new story |

## 2. Review rules for code

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
| CQ-R13 | The change stays inside the author role's paths ([agent team](agent-team.md), section 4). An edit to `ARCHITECTURE.md` from a `dev/` or `ops/` branch is confined to register rows in section 10. |
| CQ-R14 | No abstraction, configuration option, or dependency without a use in this change. |

## 3. Documentation checklist

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
