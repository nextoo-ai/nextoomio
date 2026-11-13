## Summary

Describe the change and the target release (`v0.0.1–v0.0.5`).

## Requirement IDs Covered / Affected

- List affected IDs from `SPECIFICATION.md` (e.g., `SYS-01`, `SEL-01`, `I18N-02`):

## Verification Checklist

- [ ] `python3 scripts/check_traceability.py` passes (all 78 requirement IDs and links valid)
- [ ] `node scripts/check_i18n.mjs` passes (no hardcoded UI strings; `en.json` and `pseudo-dev.json` in sync)
- [ ] `cargo test --manifest-path core/Cargo.toml` passes
- [ ] Third-party dependencies and licenses checked against `AGENTS.md` and `core/deny.toml`
- [ ] Planned vs. implemented vs. hardware-verified status is accurately documented
