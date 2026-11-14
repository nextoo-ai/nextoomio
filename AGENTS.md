# nextoomio agent rules

These rules apply to the entire repository. Follow the user's latest explicit instructions when they change a project decision, and update the relevant documents accordingly.

## Workspace and deprecated archive

- Work directly in the current project's directory. Keep source changes, documents, and working files in this checkout; do not create project copies or temporary working directories outside it unless the user explicitly requests them.
- Git worktrees are allowed only under `.claude/worktrees/` inside this checkout, for role agents that work in parallel (see "Agent team"). Remove a worktree after its pull request is merged or closed, never while it is open.
- The English documents in the project root are the maintained sources of truth.
- `SK/` contains frozen, deprecated Slovak originals retained only for historical reference. Do not synchronize, translate, update, or regenerate them to match the English documents.
- Exclude `SK/` from routine documentation maintenance, current requirement/roadmap checks, and project-wide translation passes. Read it only when historical context is specifically needed.
- Preserve the archived files unchanged unless the user explicitly asks to modify the archive.

## Language

- Communicate directly with the user in **Slovak**, including progress updates, explanations, questions, and final responses.
- Write all new and maintained project deliverables in **English**: source code identifiers, comments, documentation, specifications, roadmaps, configuration descriptions, test descriptions, generated reports, commit messages, and pull request titles/descriptions. The frozen Slovak originals in `SK/` are historical exceptions, not maintained translations.
- Write these agent rules and any other project instruction files in English as well.
- The distinction is intentional: conversation with the user is Slovak; the artifacts produced for the project are English.
- Preserve product names, protocol identifiers, externally defined names, and user-provided content in their original form where appropriate.

## WebUI language and internationalization

- English (`en`) is the initial and default WebUI language. Do not add Slovak UI copy merely because the conversation is in Slovak.
- Keep all application-authored user-facing text in an English translation catalog addressed through stable translation keys. This includes labels, navigation, validation, errors, notifications, tooltips, and accessibility text.
- Keep UI components independent of the catalog's language. A future language must be addable through a new catalog without rewriting components or business logic.
- Use interpolation and plural-aware messages instead of concatenating translated sentence fragments. Use English fallback for missing translations.
- Keep machine-readable API/MQTT identifiers and error codes stable and independent of translated presentation text. Do not translate media metadata or user-entered names automatically.
- No additional language or language selector is required while only English is available.

## Project sources of truth

- Read `SPECIFICATION.md`, `ROADMAP.md`, and `ARCHITECTURE.md` before making functional or architectural changes.
- `SPECIFICATION.md` defines behavior; `ROADMAP.md` defines cumulative delivery across v0.0.1–v0.0.5; `ARCHITECTURE.md` defines the design, the architecture decisions, and the component and license register.
- Preserve requirement IDs and their roadmap coverage. When adding or changing a requirement, update both documents and affected acceptance criteria.
- All agreed functionality must remain planned by v0.0.5. Do not silently drop requirements, move them beyond the roadmap, or label incomplete integrations as delivered.
- Distinguish planned behavior, implemented behavior, and behavior verified on real hardware. Document actual hardware/client versions and measured results.

## Agent team

- Work is split across role agents: Architect, Developer, PR Reviewer / Code Quality, QM / Verifier, and DevOps. `docs/engineering/agent-team.md` defines each role's responsibility, inputs, outputs, and limits; `.claude/agents/` holds the definitions; `docs/engineering/code-quality.md` holds the review rules.
- An agent started in a role stays within that role's limits. If a task needs something outside them, it stops and reports instead of working around the limit or the `role-guard` hook.
- State is handed over through the repository and GitHub issues, pull requests, and reviews, never through conversation history.
- No agent merges a pull request, closes an issue, pushes to `main`, or changes repository settings. The project owner approves and merges.
- Every agent reports problems outside its task as findings; only the Architect files defects. Suspected security vulnerabilities are reported to the project owner only, never in a public issue.
- Changes to these rules, the agent definitions, the guard, and the code-quality rules are made by the Architect through a reviewed pull request.

## Implementation

- Prefer small, working prototype increments over speculative abstractions or unrelated features.
- Route playback requests through one session manager. Keep configured group membership separate from the active playback owner.
- Preserve Music Assistant isolation, local satellite controls, reference-volume rules, and the local-only voice audio layer.
- Preserve the Yocto/read-only system and separate persistent data. Updates in the planned series use manual image upload and A/B recovery, without online updating or image signing.
- Deliver authentication and other required protections with the feature that needs them. Never include secrets in source control, ordinary logs, or diagnostic exports.
- Keep third-party licenses and build instructions accurate. Do not claim compatibility based solely on a component's name or related hardware.

## Dependency and license selection

- nextoomio is a non-commercial open-source project, not a planned commercial product. This describes the project's intent; do not add a non-commercial restriction to the original Apache-2.0 code or assume that free distribution removes license obligations.
- When alternatives are comparable for the required functionality, reliability, security, maintenance, target-hardware performance, and reasonable integration effort, select the one with fewer licensing and redistribution restrictions. Prefer suitable permissive licenses such as Apache-2.0, MIT, BSD, or ISC. Do not rank dependencies by license name alone: assess the actual combination and shipped artifacts.
- Apply this preference when selecting new dependencies and reviewing existing ones. Make a justified, scope-preserving substitution without asking the user to reconfirm this rule; update architecture, component/license registers, build configuration, and relevant verification together. Distinguish a planned selection from an implemented or measured replacement.
- GPL and LGPL dependencies are allowed when they are the appropriate technical choice. Do not remove required functionality, weaken security, introduce a less reliable component, or undertake a disproportionate rewrite merely to avoid copyleft. Record why a more permissive alternative is not yet comparable; revisit at the relevant integration gate.
- Avoid non-commercial-only, research-only, source-available, or unclear redistribution terms where a suitable open-source alternative exists. Check code, transitive dependencies, optional plugins, firmware, model weights, and bundled assets separately against the exact selected version. A runtime's license does not establish its models' license.
- Keep notices, corresponding-source and installation/relinking obligations, and build/license records as required even for free releases. Evaluate service terms, trademarks, and patent questions separately from software licenses. Do not introduce commercial certification or paid SDK work as a project requirement merely because it is available; record genuine constraints without silently dropping agreed features.

## Working and verification

- Continue authorized work without asking the user to reconfirm settled decisions or routine reversible changes.
- Preserve unrelated user changes. Keep changes focused on the requested outcome.
- Run checks appropriate to the change. For documentation, check requirement coverage, release assignments, links, and consistency; do not add implementation-mirroring tests for simple edits.
- For playback, grouping, updates, or hardware support, perform the relevant functional and failure checks and report any checks that could not be run.
- Never claim that a build, test, integration, or hardware measurement passed without evidence. Report concrete blockers and limitations in Slovak to the user.
