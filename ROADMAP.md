# nextoomio – roadmap v0.0.1 to v0.0.5

Document version: **1.7**  
Date: **2026-11-13**  
Status: **Plan only; no item is marked implemented or verified by this document.**  
Functional rules: [SPECIFICATION.md](SPECIFICATION.md), version 1.13.  
Architecture design: [ARCHITECTURE.md](ARCHITECTURE.md), version 0.7.  
Agent and language rules: [AGENTS.md](AGENTS.md).

## 1. Goal and planning rules

Deliver the complete agreed scope by v0.0.5: standalone playback, all required inputs, native multiroom, Music Assistant, HA Voice, API, MQTT, actions, Matter, basic WebUI, manual A/B updates, and Raspberry Pi 3+ with commonly used HiFiBerry cards.

- Releases are cumulative. Each preserves previous functionality and adds the features listed below.
- The specification defines behavior; this roadmap defines delivery. Fix defects in the next release without substituting fixes for that release's planned functionality.
- This is a non-commercial open-source project. Prefer fewer license restrictions when alternatives are technically comparable; retain justified GPL/LGPL choices when no comparable alternative is established. Record the comparison and verify replacements at the release introducing them. Free releases still satisfy license obligations; commercial certification is not added to the prototype scope.
- Do not assign release dates before implementation and hardware results are available. The sequence and release gates define the working plan.
- Deliver interface security with the interface. Do not expose API, image upload, or group control without protection and defer it to v0.0.5.
- Local control, setup, and native multiroom work without a project cloud. Losing internet must not disable available local/NAS playback, control, or the native group.
- All five releases update exclusively through manual image upload. Online updates and image signing/signature verification are outside this roadmap.
- Project artifacts are English; direct communication with the user is Slovak. WebUI initially supports only English (`en`), with translation catalogs and stable keys from v0.0.1. Every subsequent screen follows the same convention; no additional shipped language is required by v0.0.5.
- Measurement targets are in specification section 13.2. Report missed targets as missed; do not label unverified integrations or cards supported.
- At v0.0.5, an agreed function must not remain merely a design, stub, hidden option, or backlog item. Record technical obstacles as undelivered requirements and revise the plan openly rather than silently reducing scope.

## 2. Release overview

| Version | User outcome | Main dependencies |
| --- | --- | --- |
| **v0.0.1** | Play local music or radio on the selected Raspberry Pi 4 + HiFiBerry Amp+ setup, control it through an English WebUI/API, and manually update the image. | Closed reference hardware, Yocto, verified audio output, common pipeline, and session manager. |
| **v0.0.2** | Play NAS music and create a synchronized native group with a reference volume per satellite. | Working v0.0.1 foundation, audio distribution, clock synchronization, and group control. |
| **v0.0.3** | Send audio through AirPlay 2, Spotify Connect, Bluetooth, or Music Assistant with correct input/group takeover. | Common input adapters, arbitration, existing multiroom, and verified external protocols. |
| **v0.0.4** | Control the device through MQTT, run follow-up actions, and use it as an HA voice satellite. | Stable commands/states, events, and an audio branch before the local voice layer. |
| **v0.0.5** | Use verified Matter control and the complete agreed feature set on supported RPi/HiFiBerry combinations. | Completed v0.0.1–v0.0.4 functionality and Matter, compatibility, and regression verification. |

## 3. v0.0.1 – standalone player and system foundation

### Features

- Bootable Yocto image targeting **Raspberry Pi 4 + HiFiBerry Amp+**. The reference hardware is closed under specification section 2.1; the card is selected under its Volumio-aligned name “HiFiBerry Amp” (`hifiberry-amp`). Read-only system, separate writable configuration/user data, and bounded logs.
- Initial Ethernet/DHCP setup: device name, administrator account, audio output, and local volume. Restore configuration after reboot without automatic playback by default.
- Local files from a designated data path or attached USB storage: WAV/PCM, FLAC, MP3, AAC/M4A, and Ogg Vorbis. Basic file/directory selection, playback, and source-provided metadata.
- Internet radio from a supplied URL, saved stations, HTTP/HTTPS MP3/AAC, and simple M3U/PLS playlists. Specific errors for invalid URLs, unsupported formats, and unavailable sources.
- Common stereo PCM 48 kHz / 24-bit pipeline, resampling of supported inputs, local volume, and mute. Play/pause/stop only according to source capabilities; reject unsupported commands.
- One playback-session manager, at most one music owner, deterministic event ordering, session IDs, and old-source disconnection before a new source. No automatic return to the previous input when a session ends.
- Local API and WebUI: input, state, metadata, playback control, local-music/radio selection, audio settings, image version, and basic diagnostics. WebUI uses the API command model.
- English (`en`) as the only WebUI language. All application-authored copy is in a separate catalog with stable keys, including onboarding, errors, validation, tooltips, and accessibility text. Establish interpolation, plural handling, locale-aware formatting, and English fallback; do not concatenate translated sentence fragments or localize API identifiers.
- HTTPS, authenticated WebUI/API including upload, browser-session protection, hashed passwords, protected secrets in storage/logs, and least-privilege services.
- Manual image upload, completeness/format/hardware validation, inactive A/B slot installation, trial boot, and automatic rollback under UPD-06. No online updates or image signing.
- Configuration backup/restore through an authenticated interface; configuration-version detection and restoration after rollback. Updates do not overwrite user music data.
- Open-source repository containing original code, build/flashing instructions, and license information. Confirm the provisional Apache-2.0 original-code license after dependency review; external components retain their licenses.

### Preparation for later releases

- Document audio branching, session states, API, and data migrations so new inputs do not require independent output control.
- Perform short technical feasibility checks for multiroom, AirPlay 2, Spotify, an MA endpoint, HA Voice, and Matter commissioning without certification. These checks are not feature delivery; record constraints, licenses, and implementation plans. Include the license-aware alternatives in architecture section 11.1; S2/S4 compare native Sendspin against the Snapcast baseline before accepting a transport substitution.
- Enumerate target RPi 3+ models and commonly used HiFiBerry cards/revisions, using Volumio's I2S DAC identifiers and display names where the card exists there. Include relevant functions and valid board/card combinations; physically impossible combinations are not required. Add newly identified common cards rather than silently shrinking the list.
- Select reference clients/controllers for later tests and record their names and versions with results.

### Release gates

- Exact model/revision and compatibility recorded for the selected Raspberry Pi 4 + HiFiBerry Amp+ combination; successful initial boot/setup and audio playback on that hardware. API/WebUI available within 90 seconds. Record the PCB revision with the result.
- Verify files in every baseline format, HTTP and HTTPS radio, both playlist formats, and understandable rejection of unsupported input.
- At least 2 hours of playback without crashes or spontaneous switching; ordinary command acknowledgment within 300 ms and an available local file/direct stream starting within 5 seconds.
- Local control and files work without internet. Unavailable radio does not block UI or other local music.
- Verify upload/install, wrong-image rejection, interrupted upload, and unhealthy-slot rollback. Inject hangs before kernel/systemd/application readiness; verify continuous watchdog handoff, one 120-second boot-to-health budget, and at most two durably counted attempts. Inject power loss during inactive-slot writing, attempt recording, and selector commit; recover idempotently while preserving configuration/user data.
- With normal data capacity full, verify that invalid/interrupted uploads and successful install cleanup retain the reserved staging capacity. Check final SWU and expanded artifact sizes against the frozen reservation/slot budgets. Test a core restart in one OS boot without session-ID reuse or stale callback admission.
- API and upload require authentication after setup. Successfully restore a backup.
- Onboarding, normal states, validation/errors, and accessibility text are English and covered by valid catalog keys. A development-only sample catalog changes presentation without component/business-logic edits; do not ship it as another supported language.

## 4. v0.0.2 – NAS and native multiroom

### Features

- Read-only NAS over both SMB 2/3 and NFSv4: server/share/path settings, required credentials, browsing, and the same baseline formats as local playback. NAS errors do not block other inputs.
- WebUI Wi-Fi configuration from an existing Ethernet connection, network state, and diagnostics. A captive portal is not required.
- Native group with one master and at most three satellites. Select the master by IP or hostname/device name; store membership separately from active playback.
- Synchronized Master input carrying local/NAS sources and radio. Do not forward Master audio into another group; reject self-reference and detected cycles.
- Local satellite takeover without affecting others; master standalone-input changes take over its satellites. Adapter contracts already reserve the MA exception, verified with real MA in v0.0.3.
- Optional central volume control, independently stored satellite references, V = G × R / 100, defaults, and the enable/disable and entry-into-native-playback behavior from section 5.1.
- Local satellite-volume changes do not alter references or other devices. The next group-volume change reapplies references. Volume changes alone do not take over other inputs.
- Group play/pause/stop and independent local satellite control. Local/group mute use OR semantics; group unmute preserves local mute.
- Silence without failover on master loss, stale-audio disposal, reconnection, and return to the active session's current position. Respect local stop/pause and input changes.
- API/WebUI for membership, group state, references, central control, and explicit local/group command scope. Group access key and protected NAS/Wi-Fi secrets.
- First hardware-matrix expansion beyond the reference combination, including software-volume behavior with different outputs.
- Catalog-based English copy for all new network/NAS/multiroom screens, errors, and status text, including parameterized member counts.

### Release gates

- SMB and NFS connect and play; after NAS errors, control and local-source selection still work.
- Verify 2-device and then 4-device physical groups. On wired Ethernet, electrical-output skew is at most 2 ms after settling; check drift over 30 minutes.
- Rejoin the current position within 5 seconds of connection, become silent within 2 seconds of stream loss, and resume an active session within 15 seconds of stable connectivity returning.
- Pass reference-volume, central-control, volume-at-takeover, local pause/stop, group-mute, and single-satellite-loss scenarios without stopping other members. A group-muted satellite must become audible on local takeover when local mute is off, and inherit stored group mute on native re-entry.
- Delay/fail audio and control channels independently, switch sessions during recovery, and restart the core. Verify authenticated session/generation admission, old-buffer disposal, and no implicit takeover by reconnect.
- Test wrong keys/pins, forwarded authentication proofs, shared/changed IP addresses, key rotation, and expired control connections. No unauthenticated audio or IP-only fallback is acceptable. Measure synchronization through the actual authenticated tunnel.
- Local NAS, native multiroom, and control work without internet. Test Wi-Fi separately and report conditions/results.
- Upgrade from v0.0.1 and rollback preserve configuration; migrations handle new references/membership compatibly with return to the previous slot. New UI copy passes catalog checks.

## 5. v0.0.3 – receiving protocols and Music Assistant

### Features

- Spotify Connect receiver selectable in Spotify, with state and available metadata in WebUI/API.
- AirPlay 2 receiver. An optional AirPlay 1 mode is a technical choice and does not replace the AirPlay 2 requirement.
- Bluetooth audio reception with explicit pairing and paired-client management; no Bluetooth audio transmission requirement.
- Apple Music/SoundCloud reception through supported protocols, particularly AirPlay, without native catalog browsing.
- Music Assistant endpoint using a verified protocol, discovery, state, and local-takeover reporting. MA owns its grouping/distribution.
- Every new adapter uses the common session manager. Connection without audio does not take over; an old stream continuing is not a new start. Reject delayed callbacks using session identity.
- Distribute standalone master inputs to the native group. Never distribute MA through native multiroom: MA takeover stops native distribution, waiting satellites become silent, and configuration remains stored.
- MA can take over endpoints separately. A master standalone-input change, group volume/mute, or transport must not take over an endpoint currently owned by MA.
- A local new input takes over only its MA endpoint. A further new MA playback start may reclaim it; a continuing stream may not. Ending MA does not restore an old source.
- Expanded WebUI/API with input availability, Bluetooth pairing, session states, and actually supported transport commands. All application copy uses the English catalog; metadata/user names remain unchanged.
- Verify source streams, accounts, dependencies, and licenses for selected components and document client-specific limitations.

### Release gates

- Real playback from reference Spotify, AirPlay 2, and Bluetooth clients; report client names/versions. Verify Apple Music/SoundCloud through a supported protocol path.
- Real MA group of at least two endpoints and an independent native group. Pass every section 6.1 scenario, including MA on the master only and on a selected satellite.
- Verify takeover pairs across all available inputs, simultaneous starts, stale-session termination, and continuing old streams. After MA local takeover, replay old stream initialization/format changes/reconnect state; they must not reclaim playback. Confirm player-local leave and a genuinely new MA start, including immediately during leave, without stopping other endpoints.
- Verify Bluetooth audio with no AVRCP player object, delayed player status, and initially silent media. Pairing/connection alone and packets from a fenced generation must not take over. Extend group-mute isolation tests to Bluetooth and MA.
- Every delivered standalone input also plays through native multiroom; MA stays isolated. Unsupported source commands do not report false success.
- Client disconnects and unavailable accounts/services/MA cause neither crashes, old-source restoration, nor image rollback.
- Regress updates, local/NAS sources, group volume, secure pairing, and catalog coverage for normal/error states.

## 6. v0.0.4 – MQTT, actions, and Home Assistant Voice

### Features

- MQTT commands, state, and events with documented topics/payloads, unique device identity, and API-equivalent behavior. MQTT is optional; a disconnected broker does not block playback.
- Broker/credential settings and MQTT TLS with certificate verification; explicit unencrypted-local-MQTT choice. Never expose secrets in published state/logs.
- Device-ready, playback, input, volume/mute, master-availability, and update-result events from section 9. Change events occur only on actual changes.
- At most 10 configured actions per event: local commands, HTTP GET/POST, and MQTT publish. Configuration/results available in WebUI/API.
- Ordering, 0–60-second delays, 3-second execution timeout, continuation on failure, bounded queue, and loop protections from the specification. No shell scripts, automatic retries, or durable queue across reboot.
- External-amplifier activation by HTTP/MQTT on playback start. An explicitly configured startup action is the only local exception to no automatic music playback after boot by default.
- HA Voice satellite with USB microphone: microphone selection, verified wake-word mechanism, HA transport, speech capture, and response playback. Recognition/generation may run outside the RPi.
- Respond only on the activated device. The music session continues; the local voice layer changes neither input/owner, membership, nor synchronization.
- Master audio branch before local volume/mute and Voice. No response or temporary ducking reaches other rooms. The same isolation applies to MA.
- Default −20 dB ducking, approximately 200 ms attack/release, 60-second interaction limit, and cleanup on failure/cancellation. Do not overwrite current volume/mute, a new input, or user stop with old state.
- Voice music commands follow ordinary control rules; a response alone is not a new music input. Verify echo cancellation and the selected microphone path on real hardware.
- WebUI/API for MQTT, actions, USB microphone, Voice state, and interaction diagnostics without ordinary voice-recording logs. New application text remains in the English catalog.

### Release gates

- Equivalent API/WebUI/MQTT commands have the same state/scope. Test MQTT authentication, TLS, broker outage, and reconnect without replaying stale commands.
- Verify the 10-action limit, ordering, timeouts, HTTP/MQTT failures, queue overflow, restart, chained rules (a startup action that starts playback triggers the playback-started rule; a rule never re-triggers itself through a chain), and feedback-triggered rule disabling. No test may block audio.
- Successful USB-microphone voice interaction on the master, a satellite, and an MA endpoint while music plays.
- Pass all section 7.1 scenarios, including unavailable HA, missing response, volume changes, new input, and stopping music during interaction.
- Confirm through measurement/listening that responses and ducking do not leak to other audio outputs; music remains synchronized.
- Verify NAS/radio/receiver → native multiroom → Voice on one device → MQTT/HTTP action. MA remains outside native distribution. New UI states/errors pass English catalog coverage checks.

## 7. v0.0.5 – Matter, complete compatibility, and scope completion

### Features

- Matter as a controlled device, without controller/bridge duties or required certification. Actual commissioning, access management, control, and state reporting in selected verified ecosystems.
- Function → standard device type/cluster → controller matrix. Implement available control/state for all functions correctly representable with the selected standard clusters; uncovered functions remain available through API/MQTT/WebUI.
- Distinguish standard-level gaps from controller limitations. Documentation alone does not replace a working Matter integration. Equal scope in every ecosystem is not promised.
- Matter commands use the common manager and respect local/group scope, MA ownership, and actual input capabilities.
- Complete measured compatibility for target RPi 3+ and all commonly used HiFiBerry cards in the maintained list: audio output, configuration, volume/mute, reboot, updates, and multiroom.
- Required image variants, drivers, and settings for valid combinations. Do not mark a combination verified based only on related card/RPi families.
- Complete basic WebUI for all agreed functions: sources/metadata, NAS, network/audio, groups/references, Bluetooth, MA state, MQTT/actions, Voice, Matter, manual update, backup/restore, and diagnostics.
- Fully translation-ready English WebUI: every application-authored string uses catalog keys, formatting/interpolation/plurals work, and missing-key checks pass. Prove future-language addition using a development-only catalog without component/business-logic changes; no second supported language or selector is required.
- Stable documented API/MQTT contracts, errors, input capabilities, and session diagnostics. Diagnostic exports exclude passwords, tokens, group keys, and voice recordings. Machine-readable fields remain independent of UI language.
- Verified cross-release configuration migrations, rollback, backup/restore on supported hardware, and local music-data preservation.
- Open-source release with original/build sources, external-component license information, confirmed original-code license, instructions, and complete release notes.

### Release gates

- Test Matter commissioning and every function marked supported in the matrix on specific controllers. Keep group-mute control and endpoint identity stable when central volume is disabled; rejected level/combined commands must not change volume policy or mute. Verify that the selected mapping and failure responses conform to the pinned Matter revision. Functions not representable by the standard remain available through other agreed interfaces.
- Every required valid hardware combination has a result. An unverified/incompatible required card does not fulfill SYS-02; it remains blocking undelivered scope until fixed or the agreement is explicitly changed.
- Full regression of every specification requirement/scenario, including 4 devices, all inputs, MA isolation, Voice, actions, and simultaneous control through interfaces.
- At least 8 hours of mixed reference-setup operation without crashes, spontaneous switching, or unbounded queue/log growth; retain v0.0.2 synchronization targets. Every supported combination also passes basic playback/system checks.
- Manual upgrades through earlier releases and rollback after unhealthy trial boot. NAS/MA/HA/internet absence at boot does not trigger false rollback.
- Complete English application-copy/catalog audit, including onboarding, errors, accessibility, and every later screen. The development-only second catalog demonstrates component-independent translation; raw keys and UI-language-dependent API identifiers fail verification.
- No undelivered mandatory item in the coverage matrix. A known defect may remain only if it does not prevent agreed functionality or its safe use and is clearly documented.

## 8. Requirement coverage matrix

“First delivery” means the first usable implementation for then-available sources/hardware. “Full functional delivery” is the deadline for the complete requirement, including dependencies such as MA exceptions or Voice. Reverify every delivered requirement at v0.0.5. No row records completed work.

| ID | First delivery | Full functional delivery | Coverage |
| --- | --- | --- | --- |
| SYS-01 | v0.0.1 | v0.0.5 | Raspberry Pi 4 reference, then the complete RPi 3+ target matrix. |
| SYS-02 | v0.0.1 | v0.0.5 | Selected HiFiBerry Amp+ with PCB revision recorded and compatibility verified, then all required common cards and valid combinations. |
| SYS-03 | v0.0.1 | v0.0.1 | Yocto image and build. |
| SYS-04 | v0.0.1 | v0.0.1 | Read-only system and separate persistent data. |
| SYS-05 | v0.0.1 | v0.0.1 | Working A/B updates and rollback. |
| SYS-06 | v0.0.1 | v0.0.5 | Source releases, builds, and licensing for every delivered component. |
| SYS-07 | v0.0.1 | v0.0.1 | Manual images only; no online mechanism or signing in any planned release. |
| IN-01 | v0.0.1 | v0.0.1 | Local files and baseline formats. |
| IN-02 | v0.0.2 | v0.0.2 | Read-only NAS SMB 2/3 and NFSv4 browsing/playback. |
| IN-03 | v0.0.3 | v0.0.3 | Spotify Connect receiver. |
| IN-04 | v0.0.3 | v0.0.3 | AirPlay 2, including protocol-based Apple Music/SoundCloud reception. |
| IN-05 | v0.0.3 | v0.0.3 | Bluetooth reception and pairing. |
| IN-06 | v0.0.1 | v0.0.1 | HTTP/HTTPS radio and M3U/PLS. |
| IN-07 | v0.0.3 | v0.0.3 | Verified Music Assistant endpoint. |
| IN-08 | v0.0.2 | v0.0.2 | Master input by IP/hostname. |
| SEL-01 | v0.0.1 | v0.0.3 | Last new playback start wins, extended to every music input. |
| SEL-02 | v0.0.1 | v0.0.3 | Connection without playback does not take over. |
| SEL-03 | v0.0.2 | v0.0.3 | Local satellite actions, including MA isolation. |
| SEL-04 | v0.0.2 | v0.0.3 | Native-group takeover and distinct transition to MA. |
| SEL-05 | v0.0.1 | v0.0.3 | No automatic old-source return, including after MA ends. |
| SEL-06 | v0.0.1 | v0.0.3 | Single owner and stored membership separate from session. |
| SEL-07 | v0.0.1 | v0.0.3 | Common manager for every music adapter. |
| MR-01 | v0.0.2 | v0.0.2 | One master and synchronized satellites. |
| MR-02 | v0.0.2 | v0.0.2 | Stored master by IP/hostname. |
| MR-03 | v0.0.2 | v0.0.2 | Silence on loss without replacement-master election. |
| MR-04 | v0.0.2 | v0.0.3 | Independent satellite input, including new receivers. |
| MR-05 | v0.0.2 | v0.0.3 | Satellite takeover with the MA exception. |
| MR-06 | v0.0.2 | v0.0.3 | Satellite control remains local, including MA. |
| MR-07 | v0.0.2 | v0.0.2 | Central volume, references, and group transport/mute. |
| MR-08 | v0.0.2 | v0.0.3 | Retain membership; no native-group control of MA endpoints. |
| VOL-01 | v0.0.2 | v0.0.2 | Enable/disable central volume. |
| VOL-02 | v0.0.2 | v0.0.2 | Stored reference per satellite and resulting-volume calculation. |
| VOL-03 | v0.0.2 | v0.0.2 | Local changes do not overwrite references. |
| VOL-04 | v0.0.2 | v0.0.3 | Group volume does not take over other inputs or MA. |
| VOL-05 | v0.0.2 | v0.0.2 | Preserve volume/references on disable; apply group volume on enable and on entry into native playback. |
| VOL-06 | v0.0.4 | v0.0.4 | Voice ducking after music-volume calculation. |
| TRN-01 | v0.0.1 | v0.0.3 | Source capabilities and master transport for delivered standalone inputs. |
| TRN-02 | v0.0.1 | v0.0.3 | Explicit resume or waiting for an external client. |
| TRN-03 | v0.0.2 | v0.0.2 | Local satellite pause/stop and rejoin at the current position. |
| TRN-04 | v0.0.2 | v0.0.2 | Independent group/local mute. |
| TRN-05 | v0.0.2 | v0.0.3 | Explicit UI/API scope and MA-control isolation. |
| REC-01 | v0.0.1 | v0.0.4 | Restore settings without autoplay; explicit startup actions from v0.0.4. |
| REC-02 | v0.0.2 | v0.0.2 | Resume the same session after connectivity interruption. |
| REC-03 | v0.0.2 | v0.0.2 | Retry intervals and respect for local stop/input changes. |
| REC-04 | v0.0.2 | v0.0.2 | Reject cycles and a further distribution level. |
| MA-01 | v0.0.3 | v0.0.3 | Usable MA endpoint. |
| MA-02 | v0.0.3 | v0.0.3 | MA alone owns control of its path. |
| MA-03 | v0.0.3 | v0.0.3 | MA groups without nested native distribution. |
| MA-04 | v0.0.2 | v0.0.3 | Native multiroom stays independently available alongside MA. |
| MA-05 | v0.0.3 | v0.0.3 | Local takeover does not change other MA endpoints. |
| MA-06 | v0.0.3 | v0.0.3 | No MA audio in native multiroom. |
| MA-07 | v0.0.3 | v0.0.3 | MA takeover of the master stops native distribution. |
| MA-08 | v0.0.3 | v0.0.3 | New starts versus continuing streams and local arbitration. |
| VOICE-01 | v0.0.4 | v0.0.4 | USB microphone and HA Voice satellite. |
| VOICE-02 | v0.0.4 | v0.0.4 | Local-only ducking; music continues synchronized. |
| VOICE-03 | v0.0.4 | v0.0.4 | Speech capture and local response. |
| VOICE-04 | v0.0.4 | v0.0.4 | Voice layer does not take over music input/ownership. |
| VOICE-05 | v0.0.4 | v0.0.4 | Branch before the local layer and isolation during MA. |
| VOICE-06 | v0.0.4 | v0.0.4 | Remove ducking after completion, error, or timeout. |
| VOICE-07 | v0.0.4 | v0.0.4 | Preserve current volume/mute. |
| VOICE-08 | v0.0.4 | v0.0.4 | Do not restore old inputs or restart stopped music. |
| CTL-01 | v0.0.1 | v0.0.5 | API grows with features; complete contract by v0.0.5. |
| CTL-02 | v0.0.4 | v0.0.5 | MQTT commands/state/events; include v0.0.5 feature states. |
| CTL-03 | v0.0.5 | v0.0.5 | Working Matter integration and verified standard-function matrix. |
| CTL-04 | v0.0.1 | v0.0.5 | Incrementally complete basic WebUI and all settings. |
| I18N-01 | v0.0.1 | v0.0.1 | English-only initial/default WebUI; no additional shipped locale required. |
| I18N-02 | v0.0.1 | v0.0.5 | Separate catalogs and stable keys on every screen, including accessibility text. |
| I18N-03 | v0.0.1 | v0.0.5 | Interpolation, plurals, locale-aware formatting, English fallback, and key checks. |
| I18N-04 | v0.0.1 | v0.0.5 | Language-independent API/MQTT identifiers and catalog-based UI errors. |
| ACT-01 | v0.0.4 | v0.0.4 | At most 10 actions per event. |
| ACT-02 | v0.0.4 | v0.0.4 | Local, HTTP, and MQTT actions. |
| ACT-03 | v0.0.4 | v0.0.4 | Complete agreed common-event catalog. |
| UPD-01 | v0.0.1 | v0.0.1 | Manual local-WebUI upload without internet. |
| UPD-02 | v0.0.1 | v0.0.1 | No online discovery, download, or automatic updates. |
| UPD-03 | v0.0.1 | v0.0.1 | No signing/signature verification in the planned series. |
| UPD-04 | v0.0.1 | v0.0.1 | A/B and format/completeness/compatibility checks. |
| UPD-05 | v0.0.1 | v0.0.1 | Upload staging and inactive-slot writing. |
| UPD-06 | v0.0.1 | v0.0.1 | Health checks, boot limits, and rollback independent of external services. |

### Requirements without a dedicated ID

| Specification/agreement area | Delivery |
| --- | --- |
| Raspberry Pi 4 + HiFiBerry Amp+ reference | v0.0.1, after recording the PCB revision and verifying compatibility on hardware under specification section 2.1. |
| Operation without a project cloud | Local operation v0.0.1, native multiroom/NAS v0.0.2; regress every release. |
| Baseline codecs, radio playlists, and PCM pipeline | v0.0.1; same file-format baseline for NAS from v0.0.2. |
| Network and initial setup | Ethernet/DHCP and admin v0.0.1; WebUI Wi-Fi v0.0.2. |
| English artifacts and Slovak user communication | Repository-wide rules in AGENTS.md; applies to all releases. |
| Translation-ready, initially English-only WebUI | Foundation v0.0.1, every later screen uses it, full audit v0.0.5; no second supported locale required. |
| Security and secrets | API/upload v0.0.1; group key and NAS/Wi-Fi v0.0.2; clients/pairing v0.0.3; MQTT/actions/Voice v0.0.4; Matter and full regression v0.0.5. |
| Backup/restore, persistence, and rollback schemas | From v0.0.1, extended and regressed each release. |
| Apple Music/SoundCloud through receiving protocols | v0.0.3 through verified paths, without native catalogs. |
| Device count, synchronization, recovery | v0.0.2 specification targets; new inputs v0.0.3, Voice v0.0.4, complete regression v0.0.5. |
| −20 dB Voice ducking, approximately 200 ms, and 60-second limit | v0.0.4. |
| Diagnostics and errors | From v0.0.1 for delivered features; complete and secret-free v0.0.5. |
| Original/component licenses, build, and publication | Every release from v0.0.1; complete scope v0.0.5. |

## 9. What is not added by v0.0.5

Retain the original exclusions: Zigbee, Google Cast as a mandatory input, Bluetooth transmission, dedicated Apple Music/YouTube Music/SoundCloud catalog integrations, a separate direct Home Assistant player integration, Matter certification/controller/bridge, multiple masters in one native group, and automatic failover. MA and HA Voice remain mandatory independent functions.

Do not add online updates or image signing. Bit-perfect playback, DSD, multichannel audio, different satellite internal formats, a captive portal, arbitrary shell actions, and a durable automation queue are not prerequisites for these five releases. Only English is initially shipped in the WebUI; translation readiness does not commit another language to this roadmap.
