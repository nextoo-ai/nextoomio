# Contributing to nextoomio

Thank you for contributing to **nextoomio**. Please follow the rules below to keep the specification, roadmap, architecture, and implementation consistent.

## 1. Sources of Truth and Traceability

- Read [SPECIFICATION.md](SPECIFICATION.md), [ROADMAP.md](ROADMAP.md), [ARCHITECTURE.md](ARCHITECTURE.md), and [AGENTS.md](AGENTS.md) before making functional or architectural changes.
- Every functional requirement ID (`SYS-*`, `IN-*`, `SEL-*`, `MR-*`, `VOL-*`, `TRN-*`, `REC-*`, `MA-*`, `VOICE-*`, `CTL-*`, `I18N-*`, `ACT-*`, `UPD-*`) must remain covered in both `ROADMAP.md` and `ARCHITECTURE.md`.
- Run `python3 scripts/check_traceability.py`, `python3 scripts/check_doc_versions.py`, and `python3 scripts/check_evidence.py` after any documentation or evidence edit. The second compares the document versions stated in the headers of the specification, roadmap, and architecture and in the README with each document's own `Document version`, and checks that references such as "ARCHITECTURE.md section 9.2" point to a heading that exists. The third validates that evidence files under `docs/evidence/` conform to `docs/evidence/TEMPLATE.md`, have non-empty mandatory fields, and use backlog identifiers. Change a document's version and the versions stated for it in the same pull request.
- Never label a planned or scaffolded feature as implemented or hardware-verified without actual test evidence on the target hardware.

## 2. Language and Internationalization Policy

- All repository deliverables (code, comments, documentation, commit messages, pull requests, issue templates, and CI logs) must be written in **English**.
- Do not modify or translate the frozen historical files in `SK/`.
- All user-facing WebUI strings must live in `webui/src/locales/en.json` and be referenced by stable keys (`I18N-01–I18N-04`). Do not hardcode English text in Vue templates or TypeScript logic.
- Run `node scripts/check_i18n.mjs` before submitting WebUI changes.

## 3. Dependency and License Selection

- Original code is licensed under `Apache-2.0`.
- Prefer dependencies with fewer licensing and redistribution restrictions (`Apache-2.0`, `MIT`, `BSD`, `ISC`) when technically comparable in functionality, reliability, security, and performance.
- All Rust dependencies are checked with `cargo-deny` (`core/deny.toml`).
- Never commit secrets, private keys, tokens, passwords, or voice recordings.
