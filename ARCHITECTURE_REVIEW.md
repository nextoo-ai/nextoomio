# Architecture review

Date: 2026-11-13  
Reviewed baseline: SPECIFICATION.md 1.11, ROADMAP.md 1.5, ARCHITECTURE.md 0.5  
Status: Original review of architecture 0.5, with design dispositions for architecture 0.6 below. No implementation, image build, hardware measurement, or integration test was performed.

## Design changes incorporated

The user authorized incorporating the findings on 2026-11-13. Architecture 0.6, specification 1.12, and roadmap 1.6 now contain the following corrections. The original review, locations, and hashes below describe the pre-change baseline, not the current line numbers. “Addressed in design” does not mean verified on hardware.

| Finding | Disposition in architecture 0.6 | Required verification |
| --- | --- | --- |
| AR-01 | Section 3.6: continuous bootloader/kernel/systemd watchdog coverage, shared trial deadline, durable attempt accounting. | S1 and v0.0.1 failure/rollback gates; bootloader choice still requires evidence. |
| AR-02 | Sections 3.3 and 6.3: process-incarnation IDs, session-bound transport generations, fresh server/client buffers, admission barrier, control lease. | S2 and v0.0.2 asymmetric-failure and timing tests. |
| AR-03 | Section 6.3: provisioned master pin, TLS-exporter-bound key proofs, authenticated audio tunnel, no IP-only fallback. | S2 authentication/relay/revocation tests and preventive isolation. |
| AR-04 | Sections 6.3 and 7.3: effective mute derives from current native participation, independent of stored group state. | v0.0.2 local-owner tests; v0.0.3 Bluetooth/MA regression. |
| AR-05 | Section 7.3: distinct MA intent/stream lifecycle, player-local leave barrier, explicit reacquisition; ambiguous mappings block integration. | S4 exact-version protocol traces and immediate-restart/race tests. |
| AR-06 | Section 7.3: A2DP media admission without required AVRCP state, including silent frames and generation fencing. | S3 and v0.0.3 receiver tests. |
| AR-07 | Section 9.1: stable group endpoint/mute, disabled-level semantics without side effects. | S6 conformant mapping and controller tests. |
| AR-08 | Sections 3.5–3.6: retained preallocated extents, logical payload length, build size budgets, serialized transaction lifecycle. | v0.0.1 full-data and interrupted-update tests. |

The additional review clarifications are also incorporated: restart-safe identity, durable journal/commit reconciliation, scope-preserving fallback rules, version-specific service secret mounts, and explicit spike deliverables. Product scope and release allocations are unchanged. The findings are addressed at the design level; remaining protocol, boot, timing, and controller checks remain release prerequisites.

## Original review (architecture 0.5)

## Verdict

The overall architecture is a suitable basis for the prototype. Keep the single playback authority, input adapters, explicit ownership domains, separate local gain/voice stages, and shared command model. A wholesale redesign is unnecessary.

The design is not ready to be declared fully closed. This review identifies three P1 design gaps, four P2 behavior/integration issues, and one P3 reservation-lifecycle issue. Resolve each before its dependent implementation or release gate. An existing feasibility spike is useful, but does not make a fallback that weakens a mandatory requirement acceptable.

Priority definitions: P1 is a missing guarantee for recovery, isolation, or access protection; P2 is a specific functional or protocol mismatch; P3 is a smaller internal design inconsistency. These are design findings, not observed runtime defects.

## Findings

### AR-01 — P1: Trial-boot recovery starts too late

**Location:** ARCHITECTURE.md:198; related spike at :280.  
**Requirements:** UPD-06, SYS-05. **Required before:** v0.0.1 image release.

The only described hang recovery is a hardware watchdog armed by systemd. A trial kernel that hangs before PID 1 starts never reaches that arming point or the 120-second health check. The one-shot `tryboot` selection returns to the previous slot only after a reset; it does not itself cause that reset. The proposed sequence therefore does not cover all trial-boot hangs required by UPD-06.

**Recommended correction:** Specify watchdog coverage and handoff from the bootloader through the kernel to systemd, with board-specific settings and a total trial deadline. Raspberry Pi documents a bootloader/kernel watchdog handoff for Pi 4/5; do not infer equivalent Pi 3 behavior. S1 must demonstrate a working alternative where necessary. [Raspberry Pi watchdog documentation](https://www.raspberrypi.com/documentation/computers/config_txt.html#kernel_watchdog_timeout)

**Verification scenario:** Boot an intentionally broken trial image that hangs before systemd, and another that reaches systemd but never passes health checks. Both must reset automatically and stop retrying after the allowed attempts. Record the selected slot and preserved configuration after recovery.

### AR-02 — P1: Native audio has no defined binding to the playback session

**Location:** ARCHITECTURE.md:320–327; related statements at :111 and :134.  
**Requirements:** SEL-01, SEL-06, SEL-07, REC-02; specification section 4 stale-audio rule. **Required before:** v0.0.2 integration.

The control channel carries a master session ID, while recovery assumes the returning audio stream carries the same session. The design does not define how that ID is attached to, or verified against, the independent Snapcast connection. Snapcast's documented audio chunks carry timing and encoded data, without a nextoomio session identifier. A control takeover can arrive before the corresponding audio boundary, or a reconnect can deliver a different session while the satellite still holds an old control snapshot. Checking control-message IDs alone cannot prove which session buffered audio belongs to. [Snapcast binary protocol](https://raw.githubusercontent.com/snapcast/snapcast/develop/doc/binary_protocol.md)

**Recommended correction:** Define a transport generation bound to the master session and to a specific audio connection or stream. Keep output gated until the authenticated control generation and transport generation agree. Specify the ordering of unlink, server/client buffer flush, transport readiness acknowledgment, and relink. If this requires a wrapper or a component patch, identify it explicitly instead of implying native protocol support.

**Verification scenario:** Delay control while audio continues, then delay audio while takeover messages arrive; switch sources twice during the outage. No old-session sample may become audible after the new session is granted, and reconnect must not revive an ended session.

### AR-03 — P1: The stream-security fallback does not preserve group-key authentication

**Location:** ARCHITECTURE.md:321 and :575.  
**Requirements:** Specification section 10 native-group access key; ROADMAP.md section 1 protection with feature delivery. **Required before:** v0.0.2 integration.

If Snapcast authentication/TLS proves inadequate, the design permits authorizing an audio connection solely by the source IP of an authenticated control connection. That is not proof that the audio peer knows the group key. In addition, the proposed control handshake skips certificate validation and authenticates nonces/device IDs without binding the proof to the TLS channel. An active relay can forward a legitimate handshake over two TLS connections; the master then associates the authenticated control connection with the relay's source address. The proposed IP-only fallback can consequently authorize the relay's audio connection. Per-message HMACs do not repair that audio authorization boundary.

**Recommended correction:** Require an authenticated audio channel bound to the intended peer and group. Use verified native support or an authenticated tunnel/proxy; keep address filtering only as an additional restriction. For control, use a reviewed authenticated transport, or specify certificate/public-key pinning and channel-bound authentication. Offline operation does not require abandoning peer identity verification. Snapcast exposes TLS and authentication facilities that S2 should test against a pinned version rather than accepting an IP-only downgrade. [Snapcast configuration](https://raw.githubusercontent.com/snapcast/snapcast/develop/server/etc/snapserver.conf)

**Verification scenario:** Reject a peer without the group key even when it relays a valid control handshake or shares an allowed source address. Reject an unexpected server identity and verify that audio/control credentials cannot authorize the other channel accidentally.

### AR-04 — P2: Stored group mute can remain effective after local takeover

**Location:** ARCHITECTURE.md:326 and :370.  
**Requirements:** MR-04, MR-08, TRN-04, TRN-05, MA-05. **Required before:** v0.0.2; extend tests in v0.0.3.

The gain-stage rule is written as `local_mute OR group_mute`, with no ownership condition. Membership and group mute remain stored across source changes. Ignoring subsequent group messages while MA owns the device does not remove a group mute already applied before takeover. A muted native satellite can therefore switch to Bluetooth or MA and remain silent because of its old group's state, even though its local mute is off.

**Recommended correction:** Separate stored group mute from effective output mute. Use `local_mute OR (participating_in_native_playback AND stored_group_mute)`, including the master's native playback loop. Recompute atomically on ownership changes. Retain group mute for re-entry without applying it to standalone satellite or MA playback.

**Verification scenario:** Group-mute a native satellite, take it over locally, and verify audible local playback when local mute is off. Re-enter native playback and verify that the still-active group mute is inherited. Repeat with MA and with local mute enabled.

### AR-05 — P2: Sendspin stream initialization is treated as a new playback intent

**Location:** ARCHITECTURE.md:365–371.  
**Requirements:** SEL-01, SEL-02, SEL-05, MA-05, MA-08. **Required before:** v0.0.3 adapter implementation.

The table defines every Sendspin stream start for the player as a new playback start. Upstream also uses `stream/start` to change stream format during existing playback; it is not by itself evidence of a new user-directed takeover. After local takeover, a delayed stream initialization must not be reclassified as a new MA request merely because MA no longer owns the output. The general session-ID rule does not supply this missing mapping from upstream events to playback intent. [Sendspin server guide](https://www.sendspin-audio.com/build/guide/server/)

**Recommended correction:** Pin the MA/Sendspin versions and define an adapter state machine for activation, stream generation, local leave, availability, and explicit reacquisition. Document which upstream transition creates a new playback intent and which only resets a decoder or connection. Use player-local leave semantics for local stop/takeover; do not emit a controller stop that could stop the MA group. Upstream explicitly distinguishes leaving the group from declaring the player unavailable. [Sendspin client guide](https://www.sendspin-audio.com/build/guide/client/)

**Verification scenario:** After local takeover, inject an old `stream/start`, format change, and reconnect. The local source must keep ownership. A genuinely new MA request must still reclaim this endpoint without switching the other MA endpoints.

### AR-06 — P2: Bluetooth playback incorrectly requires AVRCP player state

**Location:** ARCHITECTURE.md:364.  
**Requirements:** IN-05, SEL-01, SEL-02. **Required before:** v0.0.3 adapter implementation.

The start condition requires both active A2DP transport and AVRCP `playing`. BlueZ exposes the addressed player as optional. A source can deliver audio without an available player/status object, so the documented conjunction can leave a valid Bluetooth stream permanently inaudible. The architecture itself correctly makes transport controls conditional on client support, but does not apply that distinction to reception. [BlueZ Media API](https://bluez.readthedocs.io/en/latest/media-api/)

**Recommended correction:** Recognize a new A2DP media-stream generation using actual packet/decoded-frame delivery and transport state. Use AVRCP as supporting metadata and optional control, not as a mandatory gate. Do not use nonzero sample amplitude as the start test: valid audio can begin with silence. Keep pairing/connection without media separate from playback.

**Verification scenario:** Play from an A2DP source with no AVRCP player object, and one whose player state arrives late. Both should acquire playback on media delivery; a merely paired/connected source must not acquire it.

### AR-07 — P2: Matter group mute disappears when central volume is disabled

**Location:** ARCHITECTURE.md:454.  
**Requirements:** TRN-04, CTL-03. **Required before:** v0.0.5 Matter mapping freeze.

The proposed second Speaker endpoint exposes both group volume and group mute, but exists only while central volume is enabled. Disabling central volume therefore removes a group-mute operation that the specification explicitly keeps independent of that setting. The design already identifies a standard representation for mute, so this is a product-state restriction rather than a demonstrated standard limitation.

**Recommended correction:** Keep a stable group-control representation while a native group is configured. Define group-level behavior when central volume is disabled separately from group mute, and verify that the chosen device type/cluster combination remains valid. Do not dynamically remove the mute endpoint simply because volume policy changes.

**Verification scenario:** Commission the device, configure a group, disable central volume, and toggle group mute through Matter. Native members should follow group mute, locally muted members should stay muted after group unmute, and unrelated owners should remain unaffected.

### AR-08 — P3: Rejected uploads release the promised staging reservation

**Location:** ARCHITECTURE.md:167 and :193–194.  
**Concern:** Internal consistency of the update-space guarantee. **Required before:** v0.0.1 updater completion.

The staging file is preallocated to keep music from consuming update space, but failed validation deletes it. Deletion releases the reservation. Once user data occupies that space, the next valid update no longer has the promised reserved capacity. Rejecting a later update safely would still satisfy the insufficient-space rule in UPD-05; the defect is in the stronger reservation guarantee made by the architecture.

**Recommended correction:** Retain the allocated staging extent across invalid uploads, or reserve capacity through a separate mechanism. Specify cleanup for interruption, failed validation, successful installation, and reboot. Bound the final `.swu` size against the reservation in the build; a 3 GiB system slot does not establish that its update bundle fits the proposed 1 GiB staging limit.

**Verification scenario:** Fill normal data capacity, reject an invalid upload, add more music, and upload a maximum-size supported valid image. The reserved capacity must remain available, or the documented guarantee must be corrected explicitly.

## Coverage and overall assessment

An automated requirement-ID check found all **78 specification IDs** in both the roadmap coverage matrix and the architecture traceability table, with no missing or additional IDs. Relative document links in those three root files resolve. Document-version cross-references agree: specification 1.11, roadmap 1.5, architecture 0.5.

This is traceability coverage, not proof of behavioral compliance. The issues above occur despite complete ID coverage.

| Area | Assessment |
| --- | --- |
| Release allocation | Consistent v0.0.1–v0.0.5 sequence; later integrations do not need to be delivered in v0.0.1. |
| Playback architecture | Good central ownership and adapter boundaries; finalize event/transport generation contracts. |
| Native multiroom | Suitable master loop and pre-gain distribution; session binding, mute scope, and authenticated audio need correction. |
| Central volume | Formula, per-satellite references, entry behavior, and local overrides match specification 1.11. |
| Music Assistant | Separate route correctly prevents native redistribution; upstream lifecycle mapping remains incomplete. |
| Voice | Correct local branch and independent ducking stage; process hooks, echo cancellation, and measured synchronization remain integration gates. |
| API/MQTT/actions | Shared model, bounded work, retained-command rejection, and causal action chains are appropriate. |
| Updates and persistence | Correct manual unsigned A/B direction and versioned config/secrets; early-boot recovery and reservation lifecycle need attention. |
| Hardware | Current documents consistently use RPi 4 + Amp+; broader support remains a measured matrix, not an inferred promise. |
| English/i18n | English-only catalog and locale-independent machine fields match requirements. |
| Licenses/dependencies | Process boundaries and per-package manifests are sensible; no complete pinned dependency/license audit is established by this review. |

## Additional clarifications and existing release gates

These are follow-up design details or already acknowledged feasibility work, not additional confirmed findings:

1. **Session identity across core restarts:** the documented boot-counter/sequence pair needs an explicit persistence/reset rule. An in-memory sequence can repeat after a core restart within the same OS boot. Prefer a device ID plus a fresh process-incarnation ID and sequence, or a proven durable allocator. Verify that a restarted master is never mistaken for an old session.
2. **Update transaction durability:** specify durable states for staged/installing/trial/committed/failed, when attempts are persisted, and idempotent recovery after power loss. The shared selector's sub-sector overwrite is already an S1 risk; its small size is not evidence of atomicity. Include power-loss tests during commit, not only inactive-slot writing.
3. **Scope-preserving fallbacks:** R-09's direct-ALSA fallback cannot establish final compliance unless the local gain/mute and mandatory Voice path are preserved. R-10's removal of unauthorized links after creation is weaker than preventing them; test that unauthorized audio cannot become audible before removal. Record failure as undelivered scope if necessary.
4. **Persistent paths:** section 3.5 versions secrets under `/data/secrets/v<N>/`, while section 9.1 names `/data/secrets/matter`. Declare whether the latter is shorthand or a managed version-specific mount; make service credentials and migration paths unambiguous.
5. **Integration gate outputs:** S1–S6 should each produce pinned versions, a reproducible setup, actual results, and an accepted fallback that still meets the specification. Keep the 2 ms/30-minute sync test, four-device test, MA isolation, real USB voice interaction, and controller matrix as required measurements.
6. **Closed versus unverified:** retain the confirmed Amp+ selection and the closed action-chain/volume decisions. OD-02–OD-06 and the named spikes remain unresolved technical/publication work. The introduction's “none blocking” statement must not be read as implementation or release approval.

## Recommended order of work

1. Resolve AR-01 and write the update transaction state machine before committing to the first image/boot layout; fix AR-08 while implementing staging.
2. Define session/transport generations and ownership-derived output state, including AR-02 and AR-04. These are shared foundations for later adapters.
3. Resolve AR-03 before exposing native multiroom outside one device.
4. Complete S3/S4 with the explicit Bluetooth and MA state mappings from AR-05/AR-06 before integrating receivers.
5. Keep AR-07 in the Matter mapping work; it need not block v0.0.1.

## Review scope and reproducibility

Only the maintained root specification, roadmap, architecture, and agent rules were used as project authority. The deprecated `SK/` archive and `Claude outputs/` copies were not used to override them. The original review added this report without changing its baseline. The subsequent authorized incorporation is recorded above; the hashes below preserve the original reviewed versions.

Reviewed file SHA-256 values:

```text
SPECIFICATION.md  e985713e67892645addf8838a1a17deef8d706d7eb2a9ad4174d03ed8ad2d8e3
ROADMAP.md        ad5e02dbb5d6a145594a88a2fa4140a18f72ddb4fbee0bece6c94a950a9a2299
ARCHITECTURE.md   489a826c586891f297fe6fa99268ab3fdc211326eecaafb222985fc8d4a69a15
```

Upstream references were consulted on the review date to check the specific protocol and watchdog assumptions above. They are not substitutes for testing the selected versions on the target device. No claim is made that every candidate version, hardware profile, or license entry has been independently validated.
