# Evidence: <ID> <Title>

<!--
Template for verification evidence (docs/evidence/TEMPLATE.md).
See docs/backlog/README.md (Definition of Done, items 6–8) and docs/engineering/code-quality.md (CQ-D8).

Every story or defect closed as completed must have a corresponding evidence file recorded at
docs/evidence/<phase>/<story-id>.md (e.g. docs/evidence/p0/P0-F8.5.md) or docs/evidence/defects/D-<nnn>.md.

Rules:
- Never record secrets, private keys, credentials, tokens, passwords, or addresses of private networks.
- Every mandatory field below must be filled in (not empty, whitespace, or unresolved placeholder).
- Missed targets must be recorded as missed; write "none" if all targets were met.
- State explicitly if physical access to the device was needed (or "none").
-->

> [!IMPORTANT]
> Secrets, private keys, credentials, tokens, passwords, and addresses of private networks must not be recorded in evidence.

- **Date:** <YYYY-MM-DD>
- **Image version and commit:** <image build/version and git commit, or "none (repository only); commit <hash>">
- **Hardware model and revision:** <hardware model and revision, or "none (repository only)">
- **Physical access needed:** <none, or describe physical intervention needed>

## Procedure

<Commands or procedure used to verify the story/defect acceptance criteria.
May be described here as steps/code blocks, or structured in a table with the observed results under '## Procedure and observed results'.>

## Observed result

<Observed results, output, exit codes, or measurements for each acceptance criterion.
May be described here, or structured in a table with the procedure under '## Procedure and observed results'.>

## Missed targets

<Missed targets, unverified aspects, or limitations. Write "none" if all targets were met.>
