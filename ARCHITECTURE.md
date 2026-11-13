# nextoomio – architecture design v0.0.1 to v0.0.5

Document version: **0.9**  
Date: **2026-11-13**  
Status: **Design proposal. Nothing in this document is implemented, built, or measured. Component versions are candidates as of the date above and must be pinned and re-checked when each release starts.**  
Functional rules: [SPECIFICATION.md](SPECIFICATION.md), version 1.14.  
Delivery plan: [ROADMAP.md](ROADMAP.md), version 1.9.  
Agent and language rules: [AGENTS.md](AGENTS.md).

## 1. Purpose and reading guide

This document resolves the items that the specification leaves to "technical design": the audio pipeline, the playback-session manager, the multiroom transport, the endpoint protocols, the update tooling, and the component and license selection. It does so once for the target system (sections 2–4) and then per release (sections 5–9), so that every release has a complete, buildable architecture and no release has to re-architect what an earlier one shipped.

Three levels of confidence are used and must not be mixed up:

- **Decided** – a design choice made here. It can be changed only by editing this document.
- **To verify** – a claim about a third-party component or hardware that the design depends on and that a named feasibility spike must confirm before the dependent release starts.
- **Blocking** – an unresolved design issue or failed/unperformed prerequisite that prevents its dependent release gate from passing. A documented decision is not implementation evidence. S1–S6 and the remaining items in section 12 must pass at their assigned gates.

Version 0.4 incorporates the 2026-11-13 architecture review: decisions AD-18 to AD-21, core-restart reconciliation, the satellite-initiated group channel, larger system slots with a reserved update area, versioned secrets, and data-partition recovery. Version 0.5 closes OD-07 (action chains) and OD-08 (satellite volume on entering native playback) as decided by the user and recorded in specification 1.11.

Version 0.6 incorporates AR-01–AR-08 from [ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md): continuous boot recovery, durable update transactions, retained staging capacity, session-bound authenticated native audio, ownership-scoped mute, explicit receiver lifecycle rules, and stable Matter group mute. The review report records design dispositions; hardware and integration verification remain pending.

Version 0.7 records the non-commercial project intent and the license-selection rule in AGENTS.md. It selects the named Apache-2.0 MA client/model sources, makes a more permissive native transport the preferred replacement when technical equivalence is demonstrated, and retains justified copyleft components without adding commercial-product certification work. Section 11.1 records the current comparisons.

Version 0.8 adds Phase 0 (section 4.1): the A/B base development image and remote test bench that precede v0.0.1, and decision AD-22.

Version 0.9 removes the microphone from the Phase 0 bench (a microphone is needed only for Voice) and closes OD-02: Rust for the core, with the Matter process free to use the C++ reference SDK.

Section 10 is the complete component and license register, section 11 the license-compliance design, section 12 risks and open decisions, and section 13 the requirement-to-architecture traceability for every requirement ID.

## 2. Architecture decisions

| # | Decision | Choice | Main alternatives and why they were not chosen |
| --- | --- | --- | --- |
| AD-01 | Build system and base | Yocto Project 6.0 LTS "Wrynose" (supported until April 2030), systemd, one 64-bit image family for Raspberry Pi 3/4/5. | 5.0 "Scarthgap" ends April 2028 and ships Rust 1.75; non-LTS releases are supported for about six months only. |
| AD-02 | Language of original code | Rust for original policy/control daemons; TypeScript for the WebUI. A small C++ `nx-ma` wrapper uses the selected upstream `sendspin-cpp` library without rewriting it. | C++ would work equally well for the core. Rust is preferred because the Spotify, Matter, Sendspin, and Snapcast ecosystems all have Rust libraries and the core is a network-facing daemon. Confirmed by the user (OD-02). It does not constrain separate processes such as `nx-matter`. |
| AD-03 | Audio graph | PipeWire with WirePlumber. Every input is a separate PipeWire client; `nextoomio-core` is the only policy authority and creates every link. | Plain ALSA with dmix needs a custom mixer, resampler, Bluetooth path, and echo canceller. PulseAudio is superseded. JACK has no Bluetooth or policy layer. |
| AD-04 | Playback arbitration | One session manager inside `nextoomio-core`; inputs are *adapters* that request, never seize (SEL-07). Audio gating is enforced by graph links owned by the core, not by adapter cooperation. | Letting each receiver open the sound card (the usual DIY-streamer design) cannot guarantee SEL-06 or stale-session rejection. |
| AD-05 | Files, NAS, radio engine | MPD, controlled exclusively by the core over a Unix socket. | A GStreamer-based player written for the project is more code for the same result. Symphonia (pure Rust) lacks HE-AAC, which is common in radio streams. |
| AD-06 | Native multiroom transport | Snapcast (snapserver on the master, snapclient on every member including the master's own output). | A permissively licensed Sendspin server/client combination is the preferred replacement if S2/S4 demonstrates comparable functionality, timing, security, maintenance, and integration effort (OD-03, section 11.1). Until then Snapcast is the documented baseline, not a rejected GPL dependency. RTP/AES67 with PTP conflicts with the PTP ports AirPlay 2 needs. |
| AD-07 | Music Assistant endpoint | Sendspin player using the Apache-2.0 `sendspin-cpp` client in the isolated `nx-ma` process; exact version and dependencies pinned in S4. Fallback: Music Assistant's Snapcast player provider with a second snapclient instance. | Squeezelite/slimproto works but is a legacy protocol; DLNA has no synchronization. |
| AD-08 | AirPlay 2 | Shairport Sync 5.x with NQPTP. | No other maintained open-source AirPlay 2 audio receiver exists. |
| AD-09 | Spotify Connect | librespot. | The official Spotify eSDK is closed and requires a commercial agreement. |
| AD-10 | Bluetooth reception | BlueZ with the PipeWire Bluetooth plugin (A2DP sink role), SBC baseline. | bluez-alsa duplicates what PipeWire already provides. |
| AD-11 | Home Assistant Voice | OHF Linux Voice Assistant (ESPHome protocol) as a sandboxed service; microphone and response audio go through PipeWire nodes owned by the core. | The Wyoming satellite is being replaced upstream by the ESPHome-protocol satellite. Re-implementing the ESPHome voice protocol in Rust is not justified for a prototype. |
| AD-12 | Matter | `rs-matter` in a separate `nx-matter` process; fallback to the reference SDK (connectedhomeip) if a required cluster is missing. | connectedhomeip is the reference but is a very large C++/GN build inside Yocto; matter.js needs a Node.js runtime on the device. |
| AD-13 | Update engine | SWUpdate with `.swu` images containing SHA-256 hashes and a hardware-compatibility declaration; signing disabled (UPD-03). | RAUC requires signed bundles, which contradicts UPD-03. Mender is built around a server and artifact signing. |
| AD-14 | A/B boot selection | Raspberry Pi firmware `tryboot` with `autoboot.txt`, one boot partition per slot, hardware watchdog. Fallback: U-Boot with boot counter (the `meta-swupdate-boards` reference). Must be locked in v0.0.1, see OD-04. | U-Boot adds a bootloader that the Raspberry Pi firmware does not need and leaves firmware files, `config.txt`, and overlays outside the A/B scheme. |
| AD-15 | Control plane | One command/state model in the core. REST + WebSocket API is the primary surface; WebUI, MQTT, Matter, follow-up actions, and the native-group protocol are clients of the same internal command bus. | Separate logic per interface cannot guarantee "equivalent commands have the same state/scope". |
| AD-16 | WebUI | Vue 3 single-page application built with Vite, `vue-i18n` catalogs, served as static files by the core. | Any modern framework with a mature message-format library would satisfy I18N-01–04; Vue is chosen for its first-party i18n tooling and lint rules. |
| AD-17 | Original-code license | Apache-2.0 for original code. Planned library/process boundaries support this choice; verify compatibility and obligations against the exact dependency graph before publication (section 11). | – |
| AD-18 | Audio data plane | The gain stages run in a small separate process, `nx-audio`, which has no network access and is controlled by the core over a Unix socket. It starts muted and applies gain only after the core has set it. | Hosting the filter node inside the core puts the real-time audio thread in the network-facing process, so an API or MQTT fault would glitch or stop music. A PipeWire filter-chain with built-in gain controls is the alternative if `nx-audio` proves unnecessary in v0.0.1. |
| AD-19 | Group control channel | Each satellite opens one persistent, authenticated WebSocket to its configured master. The master sends a versioned group-state snapshot on connect and changes afterwards. TLS 1.3 uses an explicitly provisioned master public-key pin; group-key proofs are bound to the TLS connection. A separate authenticated WSS tunnel carries native audio (section 6.3). Neither channel uses wall-clock time for group authentication. | Master-initiated requests need the master to track satellite addresses, give no presence signal, lose messages while a satellite is unreachable, and need a second trust direction. Timestamped HMAC fails between devices without a real-time clock or internet. |
| AD-20 | Privileged operations | One small root helper, `nx-sysd`, exposes a fixed set of operations to the core over a Unix socket: NAS mounts, firewall port changes, the hardware-configuration write, update installation, reboot, and data-partition reset. The core itself has no capabilities. | Granting the capabilities to the core would make the network-facing daemon privileged. Separate ad-hoc helpers per feature would leave the privilege boundary undefined. |
| AD-21 | Time | No local function depends on correct wall-clock time: ordering uses monotonic counters, group authentication uses pinned keys and connection-bound challenges, and Snapcast keeps its own time base. `systemd-timesyncd` sets the clock when a time server is reachable and stores the last known time on the data partition. | Raspberry Pi 3 and 4 have no battery-backed clock, and local control and native multiroom must work without internet. Only outbound TLS verification (MQTT, HTTPS radio and actions) needs a plausible clock; a failure there is reported as `clock_not_set`, not as a certificate error. |
| AD-22 | Commit rule on development images | A trial slot is committed only after the local health check passes and the deploying workstation confirms over SSH within the trial budget (`remote-confirm` mode). Release images use the local health check alone, as UPD-06 requires. | Committing on local health alone can make a bench image the default although it is unreachable, which then needs physical access. Making network reachability part of the release health check would contradict UPD-06, which forbids rollback because of network conditions. |

## 3. Target system architecture

This section describes the complete system at v0.0.5. Sections 5–9 state which parts exist in each release.

### 3.1 Process view

```
┌───────────────────────────── control clients ──────────────────────────────┐
│  WebUI (browser)   API clients   MQTT broker   Matter controllers   HA/MA  │
└───────┬────────────────┬─────────────┬──────────────┬──────────────┬───────┘
        │ HTTPS/WSS      │             │ MQTT(TLS)    │ Matter       │ ESPHome / Sendspin
┌───────▼────────────────▼─────────────▼───┐   ┌──────▼──────┐       │
│              nextoomio-core              │◄──┤  nx-matter  │       │
│  API + auth │ session manager │ router   │   └─────────────┘       │
│  volume/group model │ event bus          │                         │
│  config store │ update manager           │   ┌─────────────────────▼──────┐
│  MQTT client │ actions engine            │◄──┤ voice satellite (LVA)      │
│  adapters: files radio spotify airplay   │   └────────────────────────────┘
│            bluetooth master ma           │
└──┬─────┬──────┬───────┬───────┬──────┬───┘
   │     │      │       │       │      │      control: Unix sockets, D-Bus, JSON-RPC
┌──▼─┐ ┌─▼────┐ ┌▼──────┐ ┌─────▼┐ ┌───▼────────────┐ ┌──────────┐
│MPD │ │libre-│ │shair- │ │BlueZ │ │snapserver /    │ │Sendspin  │
│    │ │spot  │ │port + │ │      │ │snapclient      │ │player    │
│    │ │      │ │nqptp  │ │      │ │                │ │          │
└──┬─┘ └─┬────┘ └┬──────┘ └─────┬┘ └───┬────────────┘ └────┬─────┘
   └─────┴───────┴──── audio ───┴──────┴───────────────────┘
                         PipeWire graph  ──►  ALSA  ──►  HiFiBerry card
```

Every box is a separate systemd service with its own user, a restricted filesystem view, and no ambient privileges. Only `nextoomio-core` listens on the control ports. Third-party daemons expose their native control interface on Unix sockets, the system D-Bus, or the loopback interface only; this includes snapserver's JSON-RPC control, which is never reachable from the network.

Two original helper processes are not drawn: `nx-audio` (AD-18) hosts the `local.music` gain stages inside the PipeWire graph, and `nx-sysd` (AD-20) performs the privileged operations. Both accept commands only from the core over Unix sockets.

### 3.2 Audio graph

The graph runs at a fixed 48 kHz clock. PipeWire processes samples as 32-bit float, which carries 24-bit PCM without loss; the ALSA sink is opened in the widest integer format the card supports (S24_LE or S32_LE). Sources at other rates are resampled once, at their capture sink.

```
 input processes                    links are created only by nextoomio-core
 MPD ─────────────► [in.files]   ─┐
 librespot ───────► [in.spotify]  │  exactly one capture sink is
 shairport-sync ──► [in.airplay]  ├─ linked to the music bus ──► [music.bus]
 BlueZ A2DP ──────► [in.bluetooth]┘                                  │
                                                                     ├──► [mr.send] ─► snapserver ─► LAN
 snapclient ──────► [in.master] ─────────────────────────────────────┤     (native distribution only)
 Sendspin player ─► [in.ma] ─────────────────────────────────────────┤     (never linked to mr.send)
                                                                     ▼
                                        [local.music]  stage 1: logical volume V and mute
                                                       stage 2: voice ducking, ramped
                                                                     ▼
 voice response ──► [voice.out] ──────────────────────────────► [out.mix] ──► ALSA sink (HiFiBerry)
 USB microphone ──► echo canceller (reference: out.mix) ──► [voice.mic] ──► voice satellite
```

Design rules that follow from the specification:

- **Capture sinks.** Each input process plays into its own virtual sink and has no permission to link elsewhere. An unlinked capture sink drains to nowhere, so a receiver that keeps sending after losing ownership is silent without having to cooperate. This is the mechanical guarantee behind SEL-06, SEL-07, and stale-session rejection.
- **Enforcing the link policy.** This is not PipeWire's default behaviour and must be configured: WirePlumber's automatic linking policy is disabled, every input stream is pinned to its capture sink, and PipeWire access rules restrict each input's client to its own sink. WirePlumber remains only as the device and Bluetooth monitor. Inputs without a native PipeWire output (librespot, and the voice satellite in v0.0.4) connect through PipeWire's ALSA or PulseAudio compatibility layer; whether the access rules identify such clients reliably is a *to verify* item of spikes S3 and S5.
- **Multiroom branch before local processing.** `mr.send` is taken from the music bus before volume, mute, ducking, and voice mixing (VOICE-05, specification 13.3). Satellites therefore receive unducked, unattenuated music and apply their own volume.
- **Master plays through its own client.** While a device distributes audio, its local output is fed by a local snapclient connected to its own snapserver, not directly from the bus. The master is then one synchronized member like any other and no latency calibration between "direct" and "network" paths is needed.
- **Cost of the loop.** A master with registered satellites therefore plays every standalone input with the Snapcast buffer delay (initially 500 ms), whether or not a satellite is listening. Bluetooth and AirPlay video lip-sync on such a master is affected (R-08). On takeover or stop, the core flushes the stream so the buffered tail of the previous source is not played.
- **Music Assistant isolation.** `in.ma` can be linked only to `local.music`. The router has no code path that links it to `mr.send` (MA-06).
- **Voice is a layer, not an input.** `voice.out` is mixed after the gain stages and has no session, so it cannot take ownership (VOICE-04).

Routing table applied by the router for each playback owner:

| Playback owner | `mr.send` fed by | `local.music` fed by |
| --- | --- | --- |
| Idle | nothing | nothing |
| Standalone input, no registered satellites | nothing | active capture sink |
| Standalone input on a master with registered satellites | active capture sink | `in.master` (local snapclient loop) |
| Master input on a satellite | nothing (REC-04: no second level) | `in.master` (snapclient to the configured master) |
| Music Assistant | nothing (MA-07 stops distribution) | `in.ma` |

**Gain stages.** `local.music` is a small PipeWire filter node hosted by `nx-audio` (AD-18) and controlled by the core. Stage 1 applies the logical volume and mute; stage 2 applies voice ducking with its own ramp. Keeping the stages separate is what makes VOL-06 and VOICE-07 structural: removing ducking multiplies by 1 again and never rewrites the user's volume. The shared mapping from logical volume `v` (0–100) to gain is `0` for `v = 0` and `(v / 100)³` otherwise (30 % ≈ −31 dB). The curve is an initial value to be tuned on hardware; it is monotonic and identical on every path as the specification requires. Hardware mixer controls are set once to a fixed per-card level from the hardware profile and are not used for user volume.

### 3.3 Session manager

The session manager is a single-threaded state machine fed by one ordered queue.

- **Requests.** Adapters and control interfaces submit `PlaybackStartRequested`, `PlaybackEnded`, `TransportCommand`, and `ClientConnected` messages. Each is stamped on receipt with a monotonic sequence number, which is the tie-breaker required by section 4 of the specification.
- **Sessions.** A granted start creates a session with a unique ID (persistent device ID + fresh 128-bit CSPRNG process-incarnation ID + monotonic sequence number) and an owner domain: `standalone(input)`, `native(master address)`, or `music_assistant`. At most one session exists per device (SEL-06). Every core restart, including within the same OS boot, creates a new incarnation before accepting commands. Session and transport identifiers from earlier incarnations cannot be reused; failure to obtain secure randomness keeps playback gated.
- **Takeover sequence.** (1) mark the new session pending; (2) stop or disconnect the previous source according to its capabilities; (3) unlink the previous capture sink; (4) apply current volume/mute and link the new path only after its adapter/transport admission barrier is ready; (5) publish state. Audio from the new source reaches the bus only at step 4. A pending start cannot block the queue: a later start cancels it, and all asynchronous readiness callbacks must re-check the current pending/session generation before linking.
- **Stale events.** Every adapter event carries the session ID it belongs to. Events for any session other than the current one are logged and dropped. They can neither take over nor stop the current session. Third-party daemons do not know session IDs, so each adapter also tracks the daemon's own stream identity (connection, track, or process generation) and attributes an event to a session only when that identity matches; a late event from an earlier stream of the same input is then still recognized as stale.
- **Resume is not a start.** While an input owns the current session, including while it is paused, a play or resume signal from its client is a transport event of that session (TRN-02). The same signal is a new playback start only when the input does not own the current session. A resume from a phone therefore does not create a new master session and does not retake satellites under MR-05.
- **Core restart.** Sessions live in memory and graph links belong to the core's PipeWire client, so both disappear when the core stops. On every start, the core reconciles before accepting requests: it stops or disconnects every source through its adapter, removes leftover links, sets the device idle, and publishes that state. A receiver that was playing must start again to be heard, which keeps the client's view and the device consistent instead of leaving a client that shows playback on a silent device. `nx-audio` keeps the last gain values until reconciliation, but the core reapplies current volume, ownership-derived mute, and an idle Voice layer before linking audio. It never restores a stale ducking or group-mute effect onto a new owner.
- **No automatic return.** Ending a session leaves the device idle (SEL-05). The previous owner is not remembered for restoration; only "last selected source" is kept for an explicit play after stop (TRN-02).
- **Stored configuration is not ownership.** Native-group membership, registered satellites, paired Bluetooth clients, and connected Spotify/AirPlay clients are configuration or presence. Only a playback start creates a session (SEL-02, MR-08).

Adapter contract (a Rust trait in the core; each adapter translates one component's native control interface):

| Capability | Meaning |
| --- | --- |
| `detect_start` | How a *new* playback start is recognized, as opposed to a connection or a continuing stream. |
| `stop_source` | How the source is stopped or disconnected on takeover. |
| `transport` | Which of play/pause/stop/next/previous the source really supports; unsupported commands are rejected with a stable error code (TRN-01). |
| `metadata` | Which metadata the source provides. |
| `remote_start` | Whether the device can start playback itself or must report "waiting for client" (TRN-02). |

### 3.4 Control plane

- **Command and state model.** All interfaces translate into the same internal commands (for example `playback.play`, `volume.set{scope: local|group}`, `input.select`) and observe the same state tree. Scope (`local` or `group`) is an explicit field on every volume, mute, and transport command (TRN-05).
- **Local API.** `https://<device>/api/v1`, JSON over REST for commands and queries, `wss://<device>/api/v1/events` for state changes. Errors are `{ "code": "<stable_machine_code>", "details": {…} }`; codes never depend on the UI language (I18N-04). The API description is published as OpenAPI with the sources.
- **Authentication.** One administrator account created at first start; Argon2id password hash. Browser sessions use an `HttpOnly`, `Secure`, `SameSite=Strict` cookie plus a CSRF token on every state-changing request. Programmatic clients use revocable bearer tokens created in the WebUI. Before setup completes, only the setup endpoints respond.
- **Transport security.** HTTPS with a self-signed certificate and key generated on first boot and kept on the data partition; port 80 only redirects. The administrator can replace the certificate.
- **Event bus.** State changes are published once on an internal bus with "changed value only" semantics. WebSocket, MQTT state publication, follow-up actions, and Matter attribute reporting subscribe to it. Each subscriber has a bounded queue, so a slow consumer cannot block the session manager. State updates are coalesced per state path (a newer value replaces a queued older one) and are never dropped, so a slow WebSocket client or broker ends with the current state; only discrete events can overflow, and an overflow is logged.

### 3.5 Persistence, configuration, and rollback compatibility

| Mount | Content | Properties |
| --- | --- | --- |
| `/` (slot A or B) | System image | Read-only ext4. |
| `/boot` (slot A or B) | Kernel, device trees, overlays, firmware configuration | Read-only FAT; written only by the updater and the hardware-configuration helper. |
| `/data/config/v<N>/` | Configuration documents for schema version N | JSON, atomic replace, one directory per schema version. |
| `/data/secrets/v<N>/` | TLS key, API tokens, group key, NAS/Wi-Fi/MQTT credentials, Matter fabric data, Spotify cache | Versioned with the configuration schema. Directory per service, mode 0700, never read by the diagnostic exporter. |
| `/data/music/` | User's local music | Never touched by updates or restore. |
| `/data/update/` | Staged image, update state, attempt counter | Outside both system slots (UPD-05). The fixed-capacity staging file and bounded update journal are preallocated on first boot. Upload cleanup never unlinks, truncates, or punches holes in them, so user music cannot consume their reserved space. |
| `/var`, `/tmp` | Runtime state, journal | Volatile; journal size-bounded. A small bounded persistent log ring is kept under `/data/log/`. |

**Schema versioning.** An image that introduces configuration schema N+1 copies `/data/config/vN` to `/data/config/vN+1` on its first boot and migrates the copy. It never edits `vN`. After rollback, the previous slot finds its own directory exactly as it left it, which satisfies "a new version must not irreversibly overwrite configuration needed by the previous slot". Settings changed during a failed trial boot are lost, which is acceptable for a trial. Secrets follow the same rule: `/data/secrets/vN` is copied to `vN+1` and never edited by the newer image, so a changed secret format or a password changed during a trial cannot leave the previous slot with configuration that references missing or unreadable secrets. An image that finds only a newer schema than it understands (a manual downgrade) refuses to migrate, starts with its own last directory if one exists, and otherwise enters first-start setup.

**Service secret paths.** Each service receives only its selected `/data/secrets/v<N>/<service>/` subtree, optionally bind-mounted to a stable private path inside its sandbox. Unversioned paths in service configuration refer to that mount, never a second writable copy outside schema migration. The update journal uses a separately versioned backward-readable format shared by both slots.

**Data-partition recovery.** `/data` is checked and repaired at boot before it is mounted. If it cannot be mounted, the device starts in a recovery state with default configuration in memory, reports `data_unavailable`, and offers reformatting through first-start setup; it never reformats on its own. An authenticated factory reset removes `/data/config` and `/data/secrets` and keeps `/data/music` unless the user also asks to erase it. The recovery UI remains available on the known-good slot even when data is unavailable. A trial cannot be committed without the configured output and readable transaction/configuration state; section 3.6 defines return to the good slot without repeated trials.

**Backup and restore.** A backup is a versioned archive of `/data/config/v<N>` plus selected secrets, encrypted with a passphrase supplied at export time. Restore validates the schema version, migrates forward if needed, and refuses archives from a newer schema.

### 3.6 Update architecture

Partition layout (MBR, logical partitions for 5–7):

| # | Label | Type | Size (initial proposal) | Content |
| --- | --- | --- | --- | --- |
| 1 | `select` | FAT | 64 MiB | `autoboot.txt` and, on Raspberry Pi 3, the first-stage firmware files. |
| 2 | `boot_a` | FAT | 256 MiB | Firmware files, `config.txt`, kernel, device trees, overlays for slot A. |
| 3 | `boot_b` | FAT | 256 MiB | Same for slot B. |
| 5 | `root_a` | ext4, read-only | 3 GiB | System slot A. |
| 6 | `root_b` | ext4, read-only | 3 GiB | System slot B. |
| 7 | `data` | ext4 | Remainder, grown on first boot | Section 3.5. |

The slot sizes are deliberately generous because the layout cannot change after the first release (section 4), and the v0.0.4 Python runtime and a possible connectedhomeip fallback must still fit. The layout needs a card of at least 16 GB. The sizes are fixed when spike S1 passes and not later.

Update flow (single updater lock; `nx-sysd` owns all slot and selector writes):

1. **Upload.** The authenticated API streams into the already allocated `/data/update/staging.swu`; the initial capacity proposal is 1 GiB, finalized by S1. Reject an oversized declared length before upload and enforce the same cap while streaming when length is unknown. Playback continues. Persist the actual payload length separately; trailing reserved bytes are not part of the SWU container. The validation/install reader exposes only that payload length.
2. **Validate.** Check container format, every artifact's presence/hash, hardware and board compatibility, readable configuration-schema range, and boot/root expanded sizes. SHA-256 checks completeness, not origin. Bind the validated digest and length to an update transaction ID. Interrupted or invalid uploads invalidate metadata but retain all allocated extents. Successful install cleanup does the same. Builds fail if the final SWU exceeds the reservation or an artifact exceeds its slot; compression must not be assumed to make it fit.
3. **Install.** On explicit command, persist `installing`, stop playback, invoke SWUpdate to write only the inactive boot/root slots, flush writes, and read back artifact checksums. Then apply the compatible hardware include to the inactive boot slot, flush/read back that controlled change, and persist `installed` with the image digest and resulting hardware-profile digest. An interrupted install is never trial-bootable. Upload and install cannot run concurrently or replace a validated transaction's bytes.
4. **Arm trial.** Persist the attempt number before requesting the one-shot boot. Record the previous good slot, target slot, artifact digest, and transaction ID. Keep the previous slot as default. Each attempted trial consumes one of at most two attempts, including an interrupted boot request; ambiguity must not create additional attempts.
5. **Check health.** The trial has at most 120 seconds from trial-slot handoff to pass: authenticated local control/API available, WebUI assets served, PipeWire and `nx-audio` ready, configured ALSA output present and openable. Carry the elapsed boot budget through boot stages; starting the application must not restart it. NAS, MA, HA, internet, and remote network services are excluded. A timer independent of the core enforces the remaining budget.
6. **Commit.** Persist `committing` only after health passes. Switch the default using the S1-approved power-loss-safe selector operation, flush and read it back, then persist `committed`. Publication of update success follows durable commit. A small in-place FAT overwrite is not assumed atomic. If the native selector cannot preserve a bootable old/new selection under power loss, use the validated redundant boot-state/U-Boot design before the first public image.
7. **Recover.** Failed health, timeout, or a watchdog reset boots the previous slot. If fewer than two attempts were consumed, that slot persists the next attempt and requests one further trial. Otherwise it persists `failed` and remains selected. An ended transaction is never automatically retried. Retrying a failed image requires a new explicit installation transaction.

**Continuous watchdog coverage (AR-01).** S1 must configure and measure watchdog handoff from bootloader through kernel/initramfs to systemd; arming only after PID 1 starts is insufficient. On Pi 4/5, evaluate the documented bootloader watchdog, `kernel_watchdog_timeout`, and systemd runtime watchdog together with the pinned firmware/kernel. Pi 3 needs its own verified chain or bootloader alternative; do not infer support from Pi 4. Individual hang deadlines must fit within the trial budget, and a healthy PID 1 must not keep an unhealthy trial alive indefinitely. Test hangs before kernel entry, before systemd, and before application health. See the [Raspberry Pi watchdog handoff documentation](https://www.raspberrypi.com/documentation/computers/config_txt.html#kernel_watchdog_timeout).

**Durable transaction record.** Use two preallocated journal records containing format version, generation, transaction ID, state, slot identities, payload length/digest, and consumed attempts, protected by checksums. Flush the new record before acknowledging each transition; recovery selects the newest valid record. The state sequence is `receiving → staged → validated → installing → installed → trial → committing → committed`, with `failed` terminal after rejection or exhausted attempts. `staged` means a complete upload, not a validated image. A receive/install interruption cannot activate the target. Reconcile actual boot slot and durable selector with the journal: finish a verified commit idempotently, or retain/return to the recorded good slot; never reset the attempt counter on a process restart. An unreadable/ambiguous journal blocks further trials and exposes recovery, rather than guessing a new update. These rules must also work with the previous release's updater.

**Selector and configuration recovery.** Power-loss tests cover every durable boundary, especially selector commit and attempt accounting. Before health passes, hardware-profile edits must not rewrite the previous good slot; any later update of its include must be validated against that slot's kernel/profile support. Data-unavailable recovery may remain available, but a trial with unavailable configuration cannot be committed as preserving the configured output. It returns to the previous good slot, which can expose data recovery without an endless update loop.

### 3.7 Security baseline

- systemd sandboxing for every service: dedicated user, `ProtectSystem=strict`, `NoNewPrivileges`, minimal capabilities, private `/tmp`, and write access only to the service's own directory under `/data`.
- nftables default-deny inbound policy. A port is opened only while the feature that needs it is enabled.
- Privileged operations exist only in `nx-sysd` (AD-20). It accepts a fixed set of typed requests from the core's user, validates every argument itself, and offers no generic command execution.
- No SSH server, package manager, or debug tooling in release images. A separate development image variant enables them.
- Secrets are loaded through one redacting type in the core. Its `Debug`/`Display` output is always a placeholder, so secrets cannot reach logs, events, MQTT state, or diagnostic exports by accident.
- All user-supplied strings that reach other programs (URLs, share paths, hostnames, device names) are validated in the core and passed as arguments or protocol fields, never through a shell.

### 3.8 Hardware abstraction

A **hardware profile** is a declarative file per sound card, identified by the card ID and display name used in Volumio's I2S DAC list (section 9.2): device-tree overlay name and parameters, ALSA device and sample format, fixed hardware-mixer settings, static output latency for multiroom alignment, whether the card has a HAT EEPROM for auto-detection, power notes, and the compatible Raspberry Pi models. Selecting a card in the WebUI requires a reboot and uses `nx-sysd` to write the selected slot's include. Preserve the previous good slot during a trial; update its include only when that slot supports the requested profile and rollback remains valid (section 3.6). The compatibility matrix required by SYS-01 and SYS-02 is generated from the profiles plus recorded test results, so a card cannot be listed as verified without a result.

### 3.9 Source layout

| Repository path | Content | License |
| --- | --- | --- |
| `core/` | `nextoomio-core` Rust workspace: session manager, router, API, adapters, update manager, later MQTT, actions; also the `nx-audio` and `nx-sysd` helper binaries | Apache-2.0 |
| `matter/` | `nx-matter` (v0.0.5) | Apache-2.0 |
| `webui/` | Vue application and translation catalogs | Apache-2.0 |
| `meta-nextoomio/` | Yocto layer: distro, images, recipes, hardware profiles, systemd units | MIT for layer metadata (OpenEmbedded convention); patches carry the license of the component they modify |
| `docs/` | API description, MQTT contract, compatibility matrix, test reports | Apache-2.0 |

## 4. Cross-release rules

- A release adds adapters, routes, API resources, and catalog keys. It does not change the session manager's ordering rules, the routing-table structure, the partition layout, or the configuration-versioning scheme. Those are fixed in v0.0.1 because changing them later would break "manual upgrades through earlier releases".
- Every third-party component is pinned to an exact version in the layer, with its license recorded in the recipe and in section 10. A component may be listed as integrated only after its release gate has passed on real hardware.
- The early S1–S6 checks required by the v0.0.1 roadmap are followed by integration checks at each dependent release. Each spike records pinned versions, reproducible setup, measurements, failure cases, and a fallback that preserves mandatory scope. A failed check blocks its dependent release; a written design or a weaker fallback is not feature delivery.

### 4.1 Phase 0 – A/B base image and remote test bench

Phase 0 (roadmap section 2.1) builds sections 3.5 and 3.6 before any playback feature, so every later image reaches the bench device through the mechanism it will ship with.

```
 workstation ── deploy ──► SSH (development image only) ──► update engine ──► inactive boot/root slot
      ▲                                                          │ one-shot trial boot
      └────────── confirm within the trial budget ◄── health ◄───┘   no confirmation: previous slot
 bench: Raspberry Pi 4 + HiFiBerry Amp+ ─► speaker; switched power outlet
```

- **What exists.** The partition layout of section 3.6, the read-only root and data partition of section 3.5, the update transaction engine in `nx-sysd` with its durable journal, the watchdog chain, and a health service. `nextoomio-core`, PipeWire policy, MPD, the API, and the WebUI are not required. The engine is driven by a command-line interface over SSH; v0.0.1 adds the authenticated upload API in front of the same engine and changes nothing behind it.
- **Health check in Phase 0.** System target reached, data partition mounted, configured ALSA card present and openable, SSH daemon listening. v0.0.1 extends the list with the API, WebUI assets, PipeWire, and `nx-audio` (section 3.6, step 5).
- **Remote-confirmed commit (AD-22).** The deploy command waits for the device to return, checks the slot and image version over SSH, and sends the confirmation. Without it the trial ends at its deadline and the previous slot boots. The mode is a build-time property of the development image and cannot be enabled in a release image.
- **Remote access.** OpenSSH with key authentication only; the authorized key comes from the builder's local configuration. The firewall opens the SSH port only in the development image.
- **Remote audio evidence.** A test tone is played through ALSA while the check reads the sound card's playback status: the stream must be running and its hardware position must advance for the whole tone. This proves the digital path up to the amplifier, not that the speaker is audible; audible output is confirmed once by listening when someone is at the device. No microphone is part of the bench, the health check, or any device function other than Voice. Quality and synchronization still need electrical measurement at their own gates.
- **Spike S1.** Phase 0 completes S1 for Raspberry Pi 4: selector behaviour, watchdog coverage before systemd, trial deadline, attempt accounting, and power loss at each durable boundary, using the switched outlet. The Raspberry Pi 3 and 5 parts of S1 and OD-04 stay open until run on those boards.
- **Still physical.** First flash, storage or power-supply failure, and a state where neither slot boots or the selector is unreadable.

## 5. v0.0.1 – standalone player and system foundation

### 5.1 Scope of the architecture in this release

Everything in section 3 that later releases depend on is built now: the image and partition layout, the update flow, the configuration scheme, the PipeWire graph with capture sinks and the two gain stages, the session manager with session IDs, the API with authentication, and the WebUI with its catalog. Only two adapters exist: `files` and `radio`, both backed by MPD.

```
 browser ──HTTPS──► nextoomio-core ──Unix socket──► MPD ──► [in.files] ─► [music.bus] ─► [local.music] ─► [out.mix] ─► ALSA
                     │  session manager, router, API, config, update manager
                     └─► SWUpdate (on install only)
```

`in.files` is shared by the `files` and `radio` adapters because MPD is one process; they are distinct inputs in the session manager and in the API.

### 5.2 Components introduced

| Component | Role | License | Integration |
| --- | --- | --- | --- |
| Yocto Project 6.0 (openembedded-core, bitbake, meta-yocto) | Build system and base distribution | Metadata MIT; bitbake GPL-2.0-only (build host only) | Build time |
| meta-raspberrypi, meta-openembedded, meta-swupdate | Board support, multimedia/network recipes, update recipes | MIT (layer metadata) | Build time |
| Raspberry Pi firmware (`start*.elf`, `fixup*.dat`, `bootcode.bin`) | Boot firmware | Proprietary, redistributable under the Broadcom/Raspberry Pi firmware license | Binary in boot partitions |
| linux-raspberrypi kernel | Kernel, HiFiBerry drivers and overlays | GPL-2.0-only | Separate program |
| systemd, glibc, BusyBox | Base userland | LGPL-2.1-or-later; LGPL-2.1-or-later; GPL-2.0-only | Separate programs / dynamic linking |
| PipeWire, WirePlumber, PipeWire ALSA and PulseAudio compatibility layers | Audio graph, device monitor, and client compatibility for inputs without a native PipeWire output | MIT (ALSA and some plugin code LGPL-2.1-or-later) | Core and `nx-audio` link `libpipewire` dynamically |
| alsa-lib, alsa-utils | Sound-card access, diagnostics | LGPL-2.1-or-later; GPL-2.0-or-later | Library / separate program |
| MPD | Decoding and playback of files and radio, M3U/PLS, stream metadata | GPL-2.0-or-later | Separate process, MPD protocol over Unix socket |
| FFmpeg (built without GPL or non-free options) | MP3, AAC (including HE-AAC), M4A, Vorbis decoding | LGPL-2.1-or-later | Library of MPD |
| libFLAC, libvorbis, libogg | FLAC and Ogg Vorbis | BSD-3-Clause | Libraries of MPD |
| curl, OpenSSL | HTTP/HTTPS streams | curl license (MIT-style); Apache-2.0 | Libraries of MPD |
| SWUpdate | Image validation and slot writing | GPL-2.0-only (client IPC library LGPL-2.1) | Separate process, invoked by the core |
| NetworkManager | Ethernet/DHCP now, Wi-Fi from v0.0.2 | GPL-2.0-or-later daemon, LGPL-2.1-or-later library | Separate process, D-Bus |
| Avahi | mDNS name and service announcement | LGPL-2.1-or-later | Separate process, D-Bus |
| nftables | Firewall | GPL-2.0-only | Separate program |
| Rust crates: tokio, axum, hyper, rustls, serde, pipewire-rs, zbus, argon2, rcgen, tracing | Core runtime, HTTPS, PipeWire and D-Bus bindings, password hashing, certificate generation | MIT and/or Apache-2.0 (rustls also ISC) | Statically linked into the core |
| Vue 3, Vite, vue-i18n, Pinia, TypeScript | WebUI | MIT (TypeScript Apache-2.0) | Bundled static assets |

### 5.3 Design details specific to v0.0.1

- **Local files and USB.** Local music lives in `/data/music`. USB storage is mounted read-only by a udev-triggered systemd unit. Browsing is done by the core on the filesystem; MPD receives absolute paths over its local socket and returns tags. No MPD database scan is required for the prototype.
- **Radio.** The core validates the URL (scheme `http`/`https`, no credentials in logs), resolves M3U/PLS to stream URLs through MPD's playlist plugins, and maps MPD errors to stable codes: `source_unreachable`, `format_unsupported`, `playlist_invalid`. Pause is not offered for live streams (TRN-01).
- **Start detection.** For `files` and `radio` a start is always an explicit command through the core, so SEL-01 and SEL-02 are trivially met here; the general mechanism is exercised with two inputs and is ready for external receivers.
- **Startup.** The core restores configuration and volumes, leaves the session manager idle, and starts nothing (REC-01). Target: API reachable within 90 s; systemd ordering puts PipeWire, the core, and the WebUI ahead of everything not needed for control.
- **First-start setup.** Until an administrator exists, the API serves only `/api/v1/setup/*`. The setup flow sets device name, administrator password, sound card (hardware profile), and initial volume (default 30 %).
- **Internationalization.** `webui/src/locales/en.json` is the only shipped catalog. Components reference keys only; an ESLint rule rejects literal text in templates, and a CI check fails on missing or unused keys. Messages use ICU-style interpolation and plural forms; dates and numbers go through `Intl`. Fallback locale is `en`; a missing key renders the English text or, in development builds only, a visible marker. A pseudo-locale catalog is built only in development mode to prove that a second catalog needs no component changes. API error codes are mapped to catalog keys in one module.
- **Hardware.** One hardware profile is delivered for the closed reference combination Raspberry Pi 4 + HiFiBerry Amp+. Following Volumio's naming, its ID is `hifiberry-amp` and its display name is "HiFiBerry Amp"; overlay `hifiberry-amp`, mixer control `Master`. The card powers the Raspberry Pi from the amplifier supply (confirmed by the user), so the profile records the supply requirement, and the low default volume of 30 % applies because the card drives passive speakers directly.

### 5.4 Feasibility spikes required by the roadmap

| Spike | Question to answer | Feeds |
| --- | --- | --- |
| S1 `tryboot` on Raspberry Pi 3, 4, and 5 | Does the firmware A/B flow in section 3.6 work identically on a Pi 3B+, a Pi 4, and a Pi 5 with the pinned firmware, including watchdog coverage before systemd, the total trial deadline, two-attempt accounting, and power loss during selector commit? Do both kernels, all overlays, and the firmware fit the boot slot with margin? Also verify the maximum SWU/reservation budget and journal recovery with a full data partition. If any boot/recovery guarantee fails, validate U-Boot/redundant boot state or resize before the first public image. | AD-14, OD-04 |
| S2 Snapcast | Skew at electrical outputs on two devices, measured through the real path (snapclient → PipeWire → `local.music` → ALSA, with the master on its own loop) and not with snapclient writing to ALSA directly; interaction of snapclient's drift correction with PipeWire's resampling; how `mr.send` is handed to snapserver; join time, behaviour on link loss, stream flush, session-generation fencing through the authenticated tunnel, peer pinning and channel-bound group proofs, asymmetric channel failures, restart identity, and group-mute isolation. The real path includes the tunnel on remote members; IP-only authorization is not an accepted fallback. | v0.0.2 |
| S3 AirPlay 2 and Spotify | Shairport Sync + NQPTP and librespot playing into a capture sink; reliable "playing started" signals; Bluetooth A2DP reception with missing/late AVRCP player state and initially silent media. | v0.0.3 |
| S4 Sendspin | Does a headless Sendspin player on the Pi register with Music Assistant, play in a Music Assistant group, and report local stop? Pin protocol/client/server versions; prove the intent/stream separation and leave barrier in section 7.3, including delayed stream initialization, reconnect, and explicit reacquisition. Ambiguous events must not be guessed as new starts. | v0.0.3, AD-07 |
| S5 Voice | Linux Voice Assistant with a USB microphone through PipeWire nodes; which state signals are available for ducking. | v0.0.4 |
| S6 Matter | `rs-matter` Speaker endpoint commissioned on-network into Home Assistant and at least one commercial controller with test credentials, including stable group mute with central volume disabled and conformant rejection/reporting of disabled group-level changes. | v0.0.5, AD-12 |

### 5.5 Requirements covered

SYS-01 (Pi 4 reference), SYS-02 (Amp+ reference card), SYS-03, SYS-04, SYS-05, SYS-06, SYS-07, IN-01, IN-06, SEL-01, SEL-02, SEL-05, SEL-06, SEL-07, TRN-01, TRN-02, REC-01, CTL-01, CTL-04, I18N-01, I18N-02, I18N-03, I18N-04, UPD-01, UPD-02, UPD-03, UPD-04, UPD-05, UPD-06.

## 6. v0.0.2 – NAS and native multiroom

### 6.1 Architecture delta

```
 MASTER                                                     SATELLITE
 [in.files] ─► [music.bus] ─► [mr.send] ─► snapserver ══ LAN ══► snapclient ─► [in.master] ─► [local.music] ─► ALSA
                                              │                                    ▲ V = G × R / 100, ownership-scoped mute
              snapclient (loop) ◄─────────────┘
                   └─► [in.master] ─► [local.music] ─► ALSA   (V = G)

 core(master) ◄══ group control: pinned WSS, opened by satellite, channel-bound proofs ══ core(satellite)
```

Two authenticated WSS connections link the cores: group control and a tunnel for Snapcast bytes. Both are opened by the satellite, which knows the master address (MR-02). They have independent bounded queues so slow audio cannot block control, but audio admission is explicitly gated by the control session/generation. The core proxies bytes in network tasks; it does not decode or schedule samples. Snapserver streaming and RPC listen on loopback only; snapclient connects to a local proxy. The diagram's LAN segment denotes this tunnel, not an exposed Snapcast TCP port.

### 6.2 Components introduced

| Component | Role | License | Integration |
| --- | --- | --- | --- |
| Snapcast (snapserver, snapclient), candidate 0.35.x | Time-synchronized audio distribution | GPL-3.0-or-later | Separate processes; JSON-RPC and process supervision by the core |
| Kernel CIFS and NFS clients | SMB 2/3 and NFSv4 read-only mounts | GPL-2.0-only (kernel) | Mounted by a minimal privileged helper using `mount(2)`; no `cifs-utils` needed |
| wpa_supplicant | Wi-Fi authentication backend for NetworkManager | BSD-3-Clause | Separate process |
| Raspberry Pi Wi-Fi firmware (`linux-firmware-rpidistro`) | Onboard Wi-Fi | Proprietary, redistributable (Synaptics/Cypress terms; requires an explicit license-flag acceptance in the build) | Binary firmware |
| Rust crates: hmac, sha2, hkdf, reqwest, tokio-tungstenite | Group-control authentication and WebSocket client | MIT and/or Apache-2.0 | Statically linked into the core |

### 6.3 Native multiroom design

- **Roles.** Membership is configured on the satellite: it stores its master's IP address or hostname (MR-02, IN-08). The satellite registers with the master over the control channel; the master persists the list of registered satellites. Both lists are configuration and survive any change of playback owner (MR-08).
- **Control channel (AD-19).** A satellite keeps one WebSocket open to its master whenever membership is configured, whatever its current input, and reconnects with the REC-03 schedule. On connect the master sends a snapshot: group-state version, master incarnation, current master session ID (or none), transport generation, source playback state, group volume `G`, central-control flag, and stored group mute. Later changes are sent as messages carrying the next version. A satellite that reconnects therefore always converges on the current state, a joining member inherits group mute (TRN-04), and no message has to be retried. The connection state is the source of the `master available` / `master unavailable` events on the satellite (ACT-03) and of the satellite presence shown on the master. The master never needs a satellite's address.
- **Group authentication (AR-03).** The master generates a 256-bit random group key and a dedicated TLS identity. Its authenticated WebUI exports a pairing token containing the group ID, key epoch, key, master device ID, and SHA-256 SPKI pin; the administrator provisions that token on satellites alongside the master address. The key is never sent over either channel. The group control and audio paths are served by a dedicated core TLS listener on port 8443, separate from the replaceable WebUI certificate on port 443. Both WSS endpoints require TLS 1.3 and the provisioned public-key pin, with TLS handshake-signature verification. Only this pinned group identity uses a verifier independent of certificate dates/hostnames; ordinary HTTPS/MQTT verification is unchanged. A mismatched pin fails closed, with no trust-on-first-use or plaintext fallback. Replacing the master's identity or rotating the group key requires explicit re-pairing; existing channels are closed on revocation.
- **Connection-bound proofs.** Derive separate control/audio keys using HKDF from the group key and group ID/key epoch. After each TLS handshake, exchange fresh random nonces and role-separated HMAC proofs over a canonical length-prefixed transcript: protocol version, group/key epoch, both device IDs, both nonces, channel purpose, and the 32-byte TLS exporter for label `EXPORTER-nextoomio-group-v1`. No state/audio is admitted before both proofs pass. Audio additionally binds the session ID, transport generation, and current authenticated control-connection ID. Disable TLS 0-RTT; retries always perform fresh proofs. Pinning authenticates the master key and exporter binding prevents a forwarded proof from authenticating a different TLS connection. Firewall rules are defense in depth, never identity. Implementation must test the exact rustls exporter/verifier integration; see [rustls exporter API](https://docs.rs/rustls/latest/rustls/struct.ConnectionCommon.html#method.export_keying_material).
- **Audio tunnel.** The satellite requests `/api/v1/group/audio` with the authenticated session/generation binding. The master connects it only to that generation's loopback snapserver. One tunnel and one local snapclient connection carry exactly one generation; they cannot be retargeted in place. An authenticated `audio.ready` header identifies the binding before binary frames are forwarded. Reject a superseded generation, wrong control connection, or expired membership; tear down both proxy ends and discard queued frames on invalidation. The master's local snapclient follows the same generation boundary through a local socket. This is nextoomio integration code, not a claimed feature of Snapcast's native wire format.
- **Stream.** One snapserver stream fed from `mr.send`, 48 kHz / 24 bit / stereo, FLAC-encoded (lossless). End-to-end buffer starts at 500 ms on wired Ethernet and is tuned during measurement; per-card static latency comes from the hardware profile.
- **Generation boundary (AR-02).** A new master playback session invalidates the old generation, closes all its audio tunnels, unlinks the master loop, and terminates the old snapserver/client processes. Create a fresh snapserver with an empty generation-specific feed and fresh client processes; never feed a new session into a still-connected old server. The control message is `group.takeover{session_id, transport_generation}`. On accepting it, a satellite first unlinks old audio, closes its tunnel, terminates the old snapclient, and discards its decoder/proxy buffers. It may link the replacement only after the authenticated header, matching current snapshot, and client readiness agree, and volume/mute are set for the current owner. Re-check those conditions immediately before relinking. A failed or slow satellite never holds a group-wide barrier. A transport failure may create a new transport generation within the same playback session; that is recovery, not a new takeover or a volume reset.
- **Takeover (MR-05, SEL-04).** When a standalone session starts or changes on a master, the master's core sends `group.takeover{session_id, transport_generation}` to every connected satellite. Each satellite's own session manager decides: it grants the request as a start of the Master input unless its current owner is Music Assistant. A satellite acts on a master session ID only once: a session it has already been taken over by, or has left through a local input change, does not take it over again when the snapshot repeats it after a reconnect. A satellite that was unreachable when a session started is not taken over later by that session; it joins on explicit play or on the next master session. The exemption is implemented now behind the adapter contract and verified with a real Music Assistant in v0.0.3.
- **Satellite independence (SEL-03, MR-04, MR-06).** A satellite's local commands never leave the satellite. Switching its input simply creates a local session; the snapclient link is dropped.
- **Volume (VOL-01–VOL-05).** Group volume `G` and the central-control flag live on the master. Each satellite stores its own reference `R`, caches the latest group settings, and computes `V = G × R / 100` on a group-volume change only when central control is enabled and its owner is the native group; otherwise its effective volume is unchanged (VOL-04). A local volume change overwrites `V` only, until the next applicable group-volume change (VOL-03); a heartbeat or unrelated state snapshot must not erase the override. References are edited from the master's WebUI through the control channel but are stored on the satellite. While central control is enabled, the satellite also computes `V = G × R / 100` from the snapshot whenever its session manager grants a start of the Master input from another input or from idle (takeover or explicit play). It does not recompute on resume after a local pause or on relinking the same session after a stream loss; with central control disabled it keeps its local volume (VOL-05).
- **Mute and transport (TRN-03, TRN-04).** Group mute is a separate flag distributed like `G`; stage-1 mute is `local_mute OR (native_participation AND stored_group_mute)`. Native participation means a satellite currently admitted to Master playback or the master playing its own native loop; stored membership alone does not qualify. Recompute mute atomically before linking a changed owner. Leaving native playback removes only the group contribution, retaining stored group mute for re-entry and preserving local mute. Group mute stays independent of central volume. Satellite pause or stop unlinks `in.master` locally and records `local_hold = paused|stopped`; play uses the current authenticated session/generation and a cleared client path to rejoin at the current position, never an old buffered position. Pause preserves the reception session and current volume; stop ends local ownership, so subsequent play follows entry-from-idle volume rules. Neither local action stops the master.
- **Loss and recovery (MR-03, REC-02, REC-03).** The core supervises snapclient instead of relying on its internal reconnect. On stream loss, mismatched generation, or control-lease expiry, remove the link to `local.music`, close the tunnel, and discard its buffers. Control heartbeats every 500 ms renew a 1500 ms monotonic lease; failure detection and output gating together must meet the 2 s silence target even if the other channel remains connected. Reconnect attempts follow 1, 2, 4, 8, then 10 s. They are cancelled by a local input change or local stop. A returning stream is relinked only through the authenticated generation boundary, while the same logical master session remains active and `local_hold` is clear. A new incarnation/session requires a new explicit play or a valid MR-05 takeover, never automatic restoration.
- **Topology guard (REC-04).** A satellite refuses a master address that resolves to itself. A device that has registered satellites refuses to become a satellite, and the registration handshake carries the registering device's own master, so a two-device cycle is detected and rejected.

### 6.4 NAS and Wi-Fi

- NAS shares are defined as server, share/export, path, protocol, and optional credentials. The mount helper mounts them read-only with soft timeouts under `/run/nextoomio/nas/<id>`; a hung or failed share reports `nas_unreachable` and cannot block the core, which talks to the helper asynchronously. The mount helper is part of `nx-sysd`; the mount directory uses shared mount propagation so that the mounts are visible inside the sandboxes of the core and MPD. Directory browsing on a share runs on a small dedicated thread pool with a timeout, so a share that hangs after mounting cannot exhaust the core's worker threads. Credentials are stored under `/data/secrets` and passed to the kernel directly, never on a command line. SMB1 is disabled in the kernel configuration.
- Wi-Fi is configured from the WebUI over an existing Ethernet connection through NetworkManager's D-Bus API. The new profile is applied with automatic revert if the device does not get an address.

### 6.5 Requirements covered

IN-02, IN-08, SEL-03, SEL-04, MR-01, MR-02, MR-03, MR-04, MR-05, MR-06, MR-07, MR-08, VOL-01, VOL-02, VOL-03, VOL-04, VOL-05, TRN-03, TRN-04, TRN-05, REC-02, REC-03, REC-04, MA-04. Configuration schema v2 adds membership, registered satellites, references, central-control flag, group mute, NAS shares, and Wi-Fi profiles.

## 7. v0.0.3 – receiving protocols and Music Assistant

### 7.1 Architecture delta

Four adapters and their processes are added. Nothing changes in the graph structure: each receiver gets a capture sink, and the routing table in section 3.2 already contains the Music Assistant row.

### 7.2 Components introduced

| Component | Role | License | Integration |
| --- | --- | --- | --- |
| librespot, candidate 0.8.x | Spotify Connect receiver | MIT | Separate process; event hook reports to the core; audio into `in.spotify` |
| Shairport Sync 5.x (AirPlay 2 build, convolution disabled) | AirPlay 2 receiver | MIT-style, with per-file third-party terms listed in the upstream `LICENSES` file | Separate process; D-Bus/MPRIS control; audio into `in.airplay` |
| NQPTP | PTP-based timing for AirPlay 2 (UDP 319/320) | GPL-2.0 | Separate process |
| libplist, libsodium, libgcrypt, libconfig, popt, soxr | Shairport Sync dependencies | LGPL-2.1-or-later; ISC; LGPL-2.1-or-later; LGPL-2.1-or-later; MIT; LGPL-2.1-or-later | Libraries of Shairport Sync |
| BlueZ | Bluetooth stack, pairing, AVRCP | GPL-2.0-or-later daemon, LGPL-2.1-or-later library | Separate process, D-Bus |
| PipeWire Bluetooth plugin with libsbc | A2DP sink role, SBC decoding | MIT / LGPL-2.1-or-later | Plugin inside PipeWire |
| Raspberry Pi Bluetooth firmware (`bluez-firmware-rpidistro`) | Onboard Bluetooth | Proprietary, redistributable (Cypress/Synaptics terms) | Binary firmware |
| `sendspin-cpp` (selected; exact version pinned in S4) | Music Assistant endpoint | Apache-2.0; audit bundled/transitive dependencies separately | Linked into a small C++ `nx-ma` adapter process |

### 7.3 Adapter behaviour

| Input | New playback start is | Connection without playback | Stop on takeover | Transport offered |
| --- | --- | --- | --- | --- |
| Spotify Connect | librespot `playing` event while Spotify does not own the current session, or track load from a newly selected Spotify session; `playing` after `paused` inside the owned session is a resume (section 3.3) | Device selected in the Spotify app but not playing: no request | Stop the librespot session (restart the process if the pinned version offers no control call) | Play/pause/next/previous only if the pinned librespot exposes them; otherwise "controlled by client" |
| AirPlay 2 | Shairport Sync "play begins" (D-Bus property / hook) | AirPlay session connected, no audio: no request | Drop the session through the D-Bus interface | Remote-control commands as offered by the pinned Shairport Sync version; to verify |
| Bluetooth | First valid media packets/decoded frames of a new active A2DP stream generation; AVRCP is optional supporting state | Paired/connected device without media: no request | AVRCP pause when supported, then release/disconnect and fence the old media generation | AVRCP play/pause/next/previous when the phone supports them |
| Music Assistant | A verified new playback intent for this endpoint, followed by usable media; see the lifecycle below. `stream/start` alone is insufficient | Registration, initial state, format change, or reconnect alone: no request | Fence old media and perform player-local leave; never send group/controller stop | Local stop, volume, mute; more only after scope verification (TRN-05) |

Additional rules:

- **Continuing streams.** After a takeover the previous receiver may keep sending. Its capture sink is unlinked and its events carry the old session ID, so nothing happens. Only an event that the adapter classifies as a new start creates a new request (MA-08, section 6.1 scenario 5).
- **Music Assistant ownership.** A granted Music Assistant start on a master runs the normal takeover, and the routing table leaves `mr.send` unfed: native distribution stops, satellites on Master input fall silent with configuration intact (MA-07). Native commands have no playback effect while Music Assistant owns the output (MR-05, MR-08, VOL-04). The control channel can still cache the latest stored group state for future native entry; it must not apply that state to MA. On MA admission, recompute effective mute to exclude the stored native-group contribution.
- **MA intent and stream lifecycle (AR-05).** The adapter separates upstream connection/stream generations from nextoomio playback intents. States are `observing`, `owned(intent_id)`, `leaving`, and `ready_after_leave`. Format changes, decoder resets, metadata, `stream/clear`, and reconnect snapshots never create an intent. A new intent requires an ordered, verified transition from a settled nonplaying state to playback addressed to this endpoint, or explicit reassignment into a playing group, plus usable media. While MA already owns the output, format replacement/resume remains within its existing session. Media can be buffered only in bounded generation-specific storage while admission is pending.
- **Local takeover and leave barrier.** First fence the old MA generation and unlink it; then issue player-local `client/leave` on the pinned Sendspin version, retaining availability so a later request can reclaim the device. Drain old protocol messages without admitting audio. A server-confirmed solo/stopped state establishes the leave barrier; only a later verified playback/reassignment transition may create a fresh intent. Reconnect alone cannot establish a new intent. Preserve the suppressed generation/leave state across connection loss, and re-establish a nonplaying baseline before rearming. Local stop follows the same path; no controller/group stop is sent and no other MA endpoint is changed. See [Sendspin external-source and leave semantics](https://www.sendspin-audio.com/build/spec/#external-source-handling).
- **MA protocol gate.** S4 must record the exact messages, ordering/barrier evidence, and MA/client versions that distinguish a new endpoint request from continuation, including an immediate user restart during leave. If the pinned protocol cannot express that distinction reliably, a documented adapter/server extension or a verified alternative endpoint is required; implementation may not guess from `stream/start` or silently disable future MA takeover. This is an explicit integration prerequisite, not a claim that the upstream protocol already supplies a nextoomio intent ID.
- **Bluetooth media admission (AR-06).** AVRCP player/status can be absent or delayed. Admit valid A2DP media independently of it, including frames containing digital silence; connection or nonzero amplitude is not the test. Track transport and decoder generations so packets continuing after takeover remain fenced. A fresh negotiated media-start transition is required to reacquire; mere packet arrival from the old generation cannot do so.
- **AirPlay latency.** Shairport Sync aligns playback to the sender's clock assuming a known output delay. The core sets Shairport Sync's backend latency offset to the delay of the current route (direct, or through the Snapcast loop on a distributing master). Synchronization with other AirPlay 2 speakers while the device is also a native master is a *to verify* item and may be documented as a limitation.
- **Sample rates.** Spotify and AirPlay deliver 44.1 kHz; they are resampled once at the capture sink (specification section 3).
- **Bluetooth pairing.** The adapter is not discoverable by default. The WebUI opens a pairing window (default 120 s); the core's BlueZ agent accepts pairing only inside it. Paired devices are listed and removable. AAC over Bluetooth is not offered because the usual decoder (fdk-aac) carries a restrictive license; aptX through `libfreeaptx` (LGPL-2.1-or-later) is optional.
- **Service terms.** librespot works only with Spotify Premium accounts and is not an official Spotify client; AirPlay 2 support is based on protocol analysis without Apple licensing. Both are recorded as product risks in section 12.
- **Apple Music and SoundCloud** are received through AirPlay (and Bluetooth); no component is added for them.

### 7.4 Requirements covered

IN-03, IN-04, IN-05, IN-07, MA-01, MA-02, MA-03, MA-05, MA-06, MA-07, MA-08, and full delivery of SEL-01–SEL-07, MR-04, MR-05, MR-06, MR-08, VOL-04, TRN-01, TRN-02, TRN-05, MA-04. Configuration schema v3 adds receiver enable flags, device names per protocol, paired Bluetooth devices, and Music Assistant endpoint settings.

## 8. v0.0.4 – MQTT, actions, and Home Assistant Voice

### 8.1 Architecture delta

```
 event bus ─► MQTT publisher ─► broker            broker ─► MQTT command handler ─► command bus
     │
     └────► actions engine ─► local command | HTTP request | MQTT publish

 USB mic ─► echo canceller ─► [voice.mic] ─► voice satellite ─► Home Assistant (ESPHome protocol)
                 ▲ reference                        │ response audio
             [out.mix] ◄──────────────── [voice.out]┘
 voice satellite state ─► core ─► ducking (stage 2 of local.music)
```

### 8.2 Components introduced

| Component | Role | License | Integration |
| --- | --- | --- | --- |
| rumqttc | MQTT 3.1.1/5 client with TLS through rustls | Apache-2.0 | Statically linked into the core |
| OHF Linux Voice Assistant | Home Assistant voice satellite over the ESPHome protocol, wake-word detection | Apache-2.0 | Separate sandboxed process |
| Python 3 runtime, aioesphomeapi, numpy, zeroconf | Runtime and dependencies of the voice satellite | PSF-2.0; MIT; BSD-3-Clause; LGPL-2.1-or-later | Runtime of the voice satellite only |
| OHF microWakeWord runtime and ESPHome `micro-wake-word-models` collection | On-device wake word | Apache-2.0; selected model weights/manifests must be covered by the collection license and NOTICE | Loaded by the voice satellite |
| libmpv (if still required by the pinned voice satellite; built without GPL parts) | Response playback inside the voice satellite | LGPL-2.1-or-later in that configuration | Library of the voice satellite |
| webrtc-audio-processing | Acoustic echo cancellation in PipeWire's echo-cancel module | BSD-3-Clause | Plugin inside PipeWire |

openWakeWord's code is Apache-2.0, but its pre-trained models have been published under a non-commercial Creative Commons license. They are not shipped. Select models from the ESPHome `micro-wake-word-models` collection whose NOTICE explicitly covers trained weights and manifests under Apache-2.0; record each model's source revision and checksum. Do not infer that arbitrary third-party microWakeWord models have the same license. See the [collection NOTICE](https://github.com/esphome/micro-wake-word-models/blob/main/NOTICE). Re-check exact runtime/model/dependency licenses at integration time.

### 8.3 MQTT

- Topic root `nextoomio/<device_id>/`. `state/<path>` is retained JSON mirroring the API state tree; `event/<name>` is non-retained; `cmd/<command>` accepts the same payloads as the API; `status` is the availability topic with a last-will message. Machine identifiers and error codes are identical to the API's (I18N-04).
- Commands carry an optional correlation ID; the result is published on `cmd/<command>/result`. Commands that arrive while the client is reconnecting are not replayed: the client connects with a clean session and does not subscribe with persistent queues. A command message delivered with the retained flag is ignored and logged, because a clean session does not stop the broker from redelivering a retained command on every reconnect.
- TLS with certificate verification is the default; the administrator can upload a CA certificate. Unencrypted MQTT requires an explicit switch. Broker credentials are secrets and never appear in published state.
- MQTT runs in its own task with a bounded outbound queue. A missing or slow broker affects only MQTT.

### 8.4 Events and follow-up actions

- Rules are stored as `event → ordered list of ≤ 10 actions` (ACT-01). Action types: `local` (allow-listed commands: input selection, play/pause/stop, volume, mute), `http` (GET/POST, URL, headers, body), `mqtt` (topic, payload, QoS 0, no retain) (ACT-02).
- Each rule has its own sequential executor, and different rules run concurrently. A rule whose actions carry long delays (up to ten actions of 60 s each) therefore cannot hold back another rule such as "playback started → switch on the amplifier". Pending events wait in one shared queue of 20; overflow is logged and dropped for actions only. Actions of one event run in order, each after its 0–60 s delay and with a 3 s timeout; a failure is logged and the next action runs.
- **Loop protection.** Commands issued by the actions engine are tagged with their causal chain: the ordered list of rules that led to the command. The tag is carried through the command bus, the session manager, and the resulting state changes and events. The rule matcher triggers any rule that is not already in the chain, so a startup rule that starts playback triggers the playback-started rule, while a rule can never re-trigger itself directly or through other rules. A rule is never run concurrently with itself. A per-rule counter disables the rule with a diagnostic when it is triggered more than 20 times in 10 s, which also covers loops through external HTTP or MQTT feedback and any event whose chain tag was lost on the way through a third-party daemon.
- Pending actions are dropped at process exit; there is no retry and no persistence across reboot.
- `device ready` is the only event that can start music after boot, and only through an explicitly configured action (REC-01).
- HTTP actions run in the core's sandbox with the same outbound restrictions as radio URLs; shell execution does not exist as an action type.

### 8.5 Voice layer

- **Audio.** The voice satellite is configured with exactly two devices: `voice.mic` as input and `voice.out` as output. It has no access to other nodes. `voice.out` is linked only to `out.mix` (VOICE-03, VOICE-05). The echo canceller uses `out.mix` as its reference, so both music and the response are removed from the microphone signal.
- **State and ducking.** The core tracks one interaction state machine: `idle → wake → listening → processing → responding → idle`. Entering `wake` ramps stage 2 to −20 dB over about 200 ms; returning to `idle` ramps back. A 60 s watchdog started at `wake` forces `idle` (VOICE-06). The state signal comes from the voice satellite; which mechanism (event hook, small upstream patch, or the ESPHome-side state) is selected in spike S5. As a safety net, loss of the voice-satellite process also forces `idle`.
- **Isolation by construction.** Ducking touches only stage 2 of the local device, after the multiroom branch. The session manager is not involved in an interaction at all, so no input, owner, membership, or volume can change because of it (VOICE-02, VOICE-04, VOICE-07, VOICE-08). If music stops or another input takes over during an interaction, stage 2 simply returns to unity on whatever is then playing or on silence.
- **Scope limits.** The voice satellite's own media-player and music-ducking features are disabled; nextoomio is not exposed to Home Assistant as a media player through it, which would be the separate direct integration that the specification excludes. Voice recordings are never written to logs or diagnostics.
- **Music commands by voice** arrive like any other command (through Music Assistant or MQTT) and follow the ordinary rules.

### 8.6 Requirements covered

CTL-02, ACT-01, ACT-02, ACT-03, VOICE-01, VOICE-02, VOICE-03, VOICE-04, VOICE-05, VOICE-06, VOICE-07, VOICE-08, VOL-06, and full delivery of REC-01. Configuration schema v4 adds MQTT settings, rules, microphone selection, and voice settings.

## 9. v0.0.5 – Matter, complete compatibility, and scope completion

### 9.1 Matter

| Component | Role | License | Integration |
| --- | --- | --- | --- |
| rs-matter (fallback: connectedhomeip) | Matter device stack | Apache-2.0 (both) | Linked into the separate `nx-matter` process |

- `nx-matter` is a client of the core's API over a Unix socket with a dedicated token. It contains no playback logic; every Matter command becomes an ordinary command and goes through the session manager with explicit scope (AD-15).
- **Commissioning.** On-network commissioning over Ethernet or Wi-Fi with a setup code and QR code shown in the WebUI; no Bluetooth commissioning. Fabric credentials are stored under `/data/secrets/v<N>/matter/` and exposed only through the service-private mount defined in section 3.5. Multiple fabrics are allowed. Uncertified test credentials are used; how each commercial controller treats an uncertified device is part of the controller matrix.
- **Initial function mapping** (to be extended to the maximum the standard allows, CTL-03):

| Function | Matter representation | Notes |
| --- | --- | --- |
| Local volume | Speaker device type, Level Control cluster | 0–100 % mapped to the level range. |
| Local mute | Speaker device type, On/Off cluster | Per the device-type definition, Off means muted. |
| Group volume and group mute (configured native master) | Stable second Speaker endpoint labelled as the group | Keep its endpoint ID and group mute when central volume is disabled. Level changes are accepted only while central control is enabled; otherwise return a conformant failure without changing volume, enabling central control, or clearing mute. |
| Play / pause / stop and playback state | Media Playback cluster | Only if valid for the chosen device type in the Matter revision current at design time and accepted by controllers; a newer streaming-speaker device type was announced as in development and must be checked. |
| Input selection, references, membership, radio/file selection, actions, update | No suitable standard cluster on a speaker | Remain on API, MQTT, and WebUI. |

- **Stable group control (AR-07).** Commissioning and the central-volume toggle must not add/remove or renumber the group endpoint. Retain it while a group is configured, including when playback is idle; group mute remains a stored native setting. Select and test a standard-conformant disabled-level response for the pinned Matter revision; reject combined level/on-off commands atomically when their level operation is disabled, so they cannot change mute as a side effect. If the proposed Speaker mapping cannot express this, S6 must choose another conformant mapping preserving independent group mute. A controller limitation does not justify removing the function from other supported controllers.
- The deliverable is the function → device type/cluster → controller matrix with test results per controller, separating "not in the standard" from "not supported by this controller".

### 9.2 Hardware completion

- Image family: one 64-bit machine configuration covering Raspberry Pi 3B, 3B+, 3A+, 4B, 400, Compute Module 4 carriers with a 40-pin header, and 5, if spike S1 and the pinned board-support layer allow it; otherwise separate machine builds sharing all recipes. The update compatibility string distinguishes builds that are not interchangeable.
- Hardware profiles use the same card identifiers and display names as Volumio's I2S DAC list for Raspberry Pi, so a user moving from Volumio finds the card under the name already known. The table lists every HiFiBerry-branded entry of that list as read on 2026-11-13; it is the candidate list for SYS-02. Only identifiers, names, overlay names, and mixer-control names are aligned; Volumio's file is not copied into the project. Overlays and mixer controls must still be confirmed against the pinned kernel, and a card is supported only after its measured result is in the compatibility matrix. HiFiBerry cards missing from Volumio's list get a new identifier in the same style.

| Profile ID | Display name | Overlay | Hardware mixer control |
| --- | --- | --- | --- |
| `hifiberry-amp` | HiFiBerry Amp | `hifiberry-amp` | `Master` |
| `hifiberry-amp100` | HiFiBerry Amp100 | `hifiberry-amp100` | `Digital` |
| `hifiberry-amp100-pi5` | HiFiBerry Amp100 [Pi5] | `hifiberry-amp100,slave` | `Digital` |
| `hifiberry-amp2` | HiFiBerry Amp2 | `hifiberry-dacplus` | `Digital` |
| `hifiberry-amp2-pi5` | HiFiBerry Amp2 [Pi5] | `hifiberry-dacplus-std` | `Digital` |
| `hifiberry-amp3` | HiFiBerry Amp3 | `hifiberry-amp3` | none |
| `hifiberry-amp4` | HiFiBerry Amp4 | `hifiberry-dacplus` | `Digital` |
| `hifiberry-amp4-pi5` | HiFiBerry Amp4 [Pi5] | `hifiberry-dacplus-std` | `Digital` |
| `hifiberry-amp4-pro` | HiFiBerry Amp4 Pro | `hifiberry-amp4pro` | `Digital` |
| `hifiberry-amp4-pro-pi5` | HiFiBerry Amp4 Pro [Pi5] | `hifiberry-amp4pro,slave` | `Digital` |
| `hifiberry-dac` | HiFiBerry DAC | `hifiberry-dac` | none |
| `hifiberry-dacplus` | HiFiBerry DAC Plus | `hifiberry-dacplus` | `Digital` |
| `hifiberry-dacplus-pi5` | HiFiBerry DAC Plus [Pi5] | `hifiberry-dacplus-std` | `Digital` |
| `hifiberry-dacplusadc` | HiFiBerry DAC Plus ADC | `hifiberry-dacplusadc` | `Digital` |
| `hifiberry-dacpluspro` | HiFiBerry DAC+ Pro | `hifiberry-dacplus` | `Digital` |
| `hifiberry-dacpluspro-pi5` | HiFiBerry DAC+ Pro [Pi5] | `hifiberry-dacplus-pro` | `Digital` |
| `hifiberry-dacplusadcpro` | HiFiBerry DAC Plus ADC PRO | `hifiberry-dacplusadcpro` | `Digital` |
| `hifiberry-dacplusadcpro-pi5` | HiFiBerry DAC Plus ADC PRO [Pi5] | `hifiberry-dacplusadcpro,slave` | `Digital` |
| `hifiberry-dacplusdsp` | HiFiBerry DAC Plus DSP | `hifiberry-dacplusdsp` | `Digital` |
| `hifiberry-dac2hd` | HiFiBerry DAC2 HD | `hifiberry-dacplushd` | `DAC` |
| `hifiberry-dac2pro` | HiFiBerry DAC2 Pro | `hifiberry-dacplus` | `Digital` |
| `hifiberry-dac2pro-pi5` | HiFiBerry DAC2 Pro [Pi5] | `hifiberry-dacplus-pro` | `Digital` |
| `hifiberry-digi` | HiFiBerry Digi | `hifiberry-digi` | none |

- Volumio keeps separate `[Pi5]` entries where Raspberry Pi 5 needs a different overlay or overlay parameter. nextoomio keeps the same identifiers but selects the matching profile automatically from the detected board model, so the user sees one card name per card.
- The mixer control is recorded only so the profile can set it once to a fixed level; user volume stays in the software gain stage (section 3.2).
- Each profile is exercised by the same automated per-combination check: boot, card detection, playback of the reference signal, volume/mute, reboot persistence, update and rollback, and membership in a native group. Results populate the matrix.

### 9.3 Completion work

- WebUI: all remaining screens, the Matter commissioning screen, the open-source license screen, and the full catalog audit (I18N-02, I18N-03).
- API and MQTT contracts frozen and published; diagnostic export with the redaction rules of section 3.7.
- Upgrade-path test images for every earlier release to verify migration chains v1 → v5 of the configuration schema and rollback at each step.
- Release artifacts: images, `.swu` files, source archive for all components, license manifest, SBOM, build instructions.

### 9.4 Requirements covered

CTL-03, and full delivery of SYS-01, SYS-02, SYS-06, CTL-01, CTL-02, CTL-04, I18N-02, I18N-03, I18N-04. Configuration schema v5 adds Matter settings.

## 10. Component and license register

"Coupling" states how the component relates to the Apache-2.0 original code: **process** (separate program, communicates over IPC), **dynamic** (shared library linked by original code), **static** (compiled into an original binary), **plugin** (loaded by a third-party program), **binary** (redistributed firmware), **build** (build host only). License identifiers are SPDX where one exists. Every entry must be re-checked against the pinned version's own license files; the Yocto recipe's `LICENSE` field and checksum are the enforcement point.

| Component | First release | License | Coupling |
| --- | --- | --- | --- |
| nextoomio-core, nx-audio, nx-sysd, nx-matter, WebUI, documentation | v0.0.1 / v0.0.5 | Apache-2.0 | – |
| meta-nextoomio layer metadata | v0.0.1 | MIT | build |
| openembedded-core, meta-yocto, meta-raspberrypi, meta-openembedded, meta-swupdate | v0.0.1 | MIT (metadata) | build |
| BitBake | v0.0.1 | GPL-2.0-only | build |
| Raspberry Pi boot firmware | v0.0.1 | Proprietary, redistributable | binary |
| Linux kernel (linux-raspberrypi) | v0.0.1 | GPL-2.0-only WITH Linux-syscall-note | process |
| glibc | v0.0.1 | LGPL-2.1-or-later | dynamic |
| systemd | v0.0.1 | LGPL-2.1-or-later | process |
| BusyBox | v0.0.1 | GPL-2.0-only | process |
| PipeWire, including its ALSA and PulseAudio compatibility layers | v0.0.1 | MIT; some modules LGPL-2.1-or-later | dynamic + process |
| WirePlumber | v0.0.1 | MIT | process |
| alsa-lib / alsa-utils | v0.0.1 | LGPL-2.1-or-later / GPL-2.0-or-later | dynamic / process |
| MPD | v0.0.1 | GPL-2.0-or-later | process |
| FFmpeg (LGPL configuration) | v0.0.1 | LGPL-2.1-or-later | library of third-party processes |
| libFLAC, libvorbis, libogg | v0.0.1 | BSD-3-Clause | library of third-party processes |
| curl | v0.0.1 | curl | library of third-party processes |
| OpenSSL | v0.0.1 | Apache-2.0 | library of third-party processes |
| SWUpdate | v0.0.1 | GPL-2.0-only; IPC client library LGPL-2.1-or-later | process |
| NetworkManager | v0.0.1 | GPL-2.0-or-later; libnm LGPL-2.1-or-later | process |
| Avahi | v0.0.1 | LGPL-2.1-or-later | process |
| nftables | v0.0.1 | GPL-2.0-only | process |
| Rust crates (tokio, axum, hyper, rustls, serde, pipewire-rs, zbus, argon2, rcgen, tracing, later hmac, sha2, hkdf, reqwest, tokio-tungstenite, rumqttc) | v0.0.1–v0.0.4 | MIT and/or Apache-2.0; rustls also ISC | static |
| Vue, Vite, vue-i18n, Pinia / TypeScript | v0.0.1 | MIT / Apache-2.0 | static (bundled) / build |
| Snapcast | v0.0.2 | GPL-3.0-or-later | process |
| wpa_supplicant | v0.0.2 | BSD-3-Clause | process |
| Raspberry Pi Wi-Fi and Bluetooth firmware | v0.0.2 / v0.0.3 | Proprietary, redistributable | binary |
| librespot | v0.0.3 | MIT | process |
| Shairport Sync | v0.0.3 | MIT-style; third-party parts per upstream `LICENSES` | process |
| NQPTP | v0.0.3 | GPL-2.0 | process |
| libplist, libgcrypt, libconfig, soxr | v0.0.3 | LGPL-2.1-or-later | library of third-party processes |
| libsodium / popt | v0.0.3 | ISC / MIT | library of third-party processes |
| BlueZ | v0.0.3 | GPL-2.0-or-later; library LGPL-2.1-or-later | process |
| libsbc | v0.0.3 | LGPL-2.1-or-later | plugin |
| `sendspin-cpp` | v0.0.3 | Apache-2.0; separate dependency audit | static in `nx-ma` |
| OHF Linux Voice Assistant | v0.0.4 | Apache-2.0 | process |
| Python 3, aioesphomeapi, numpy, zeroconf | v0.0.4 | PSF-2.0, MIT, BSD-3-Clause, LGPL-2.1-or-later | runtime of a third-party process |
| OHF microWakeWord runtime; ESPHome `micro-wake-word-models` weights/manifests | v0.0.4 | Apache-2.0 for the selected sources; retain NOTICE | data/library of a third-party process |
| libmpv (LGPL configuration, only if required) | v0.0.4 | LGPL-2.1-or-later | library of a third-party process |
| webrtc-audio-processing | v0.0.4 | BSD-3-Clause | plugin |
| rs-matter (fallback connectedhomeip) | v0.0.5 | Apache-2.0 | static in `nx-matter` |

Selected configurations avoid unnecessary licensing obligations: use FFmpeg's native AAC decoder rather than fdk-aac, keep FFmpeg/libmpv in their selected LGPL configurations, omit unused Shairport convolution, and use the named Apache-2.0 wake-word models rather than non-commercial openWakeWord weights. `cifs-utils` and Samba client libraries are omitted because the selected NAS path does not need them; GPL is not categorically prohibited. RAUC remains excluded for its signing requirement under the current design, not because of its LGPL license. Validate the full built dependency graph rather than treating these exclusions as a blanket license clearance.

## 11. License compliance design

1. **Original code and dependency compatibility.** Apache-2.0 remains the selected original-code license. The planned Rust/C++ dependencies use permissive libraries and dynamic LGPL libraries; GPL components run as separate services. Check the actual coupling and exact licenses before distribution: a process boundary alone is not automatic proof that every combination is legally independent. The non-commercial intent does not change third-party obligations or add a non-commercial restriction to nextoomio.
2. **The image is an aggregate, not an Apache-2.0 work** (specification section 11). Each release publishes the per-package license manifest generated by the build, the license texts inside the image, a WebUI screen listing them, and an SPDX SBOM.
3. **Corresponding source.** The build archives the exact sources and patches of every copyleft component for each released image and publishes them next to the image. Build instructions and the layer are in the repository.
4. **GPL-3.0 components.** Snapcast is GPL-3.0-or-later. Where GPLv3 installation-information requirements apply, provide the necessary build/install procedures and any other required information so recipients can install and run modified covered software. Unsigned images and an accessible SD card support that design but are not, by themselves, proof of full compliance. If image signing or boot locking is ever introduced, this must be revisited first.
5. **Static linking of LGPL code is avoided.** LGPL libraries are used as shared objects.
6. **Non-free binary firmware.** Raspberry Pi boot firmware and the onboard Wi-Fi/Bluetooth firmware are redistributable but not open source. The published image therefore cannot be described as consisting solely of open-source software; the release notes must say so. The build must explicitly accept the corresponding license flags.
7. **Codec patents are separate from copyright licenses.** MP3 patents have expired. AAC, in particular HE-AAC, may still be subject to patent licensing in some jurisdictions; the FFmpeg recipe is flagged accordingly in Yocto and requires explicit acceptance. This needs a decision by the publisher before distributing binary images (OD-06); it is not legal advice.
8. **Third-party services and marks.** Spotify Connect through librespot, AirPlay 2 through Shairport Sync, Bluetooth, and Matter all involve terms or trademarks outside software licensing: no official logos or "works with" claims without the respective program, and Matter is uncertified by decision.
9. **Automated gates.** `cargo-deny` (license allow-list) for Rust, a license checker for the WebUI bundle, and Yocto's `INCOMPATIBLE_LICENSE`/license-flag mechanism for the image. A new dependency with a license outside the allow-list fails the build.

### 11.1 License-aware component choices

Prefer a component with fewer restrictions when it is technically comparable; do not trade away agreed behavior or a maintainable prototype for a license label. The project is not being prepared as a commercial product. This policy guides selection and does not waive notices, source distribution, service terms, or other applicable obligations.

| Area | Selection / alternative | Decision and evidence needed |
| --- | --- | --- |
| Wake-word models | Named ESPHome microWakeWord collection (Apache-2.0), not openWakeWord pretrained weights (non-commercial terms) | Selected source is explicit in sections 8.2/10. Pin model revisions and verify real microphone/wake-word behavior in S5. No unreviewed model substitution through a downloader. |
| MA endpoint | `sendspin-cpp` (Apache-2.0), replacing the unspecified client-language/license choice | Select this documented host-capable client for `nx-ma`; verify S4 lifecycle/isolation and transitive dependencies. An alternative client needs comparable evidence, not merely a preferred implementation language. [Upstream client](https://github.com/Sendspin/sendspin-cpp). |
| Native multiroom | Sendspin server/client under permissive licenses versus Snapcast (GPLv3) | Prefer Sendspin if S2/S4 proves local standalone operation without MA, four-device timing/recovery, authenticated generation fencing, ownership/Voice isolation, acceptable resource use, and comparable integration effort. Evaluate the Apache-2.0 Python reference server and selected C++ client; the C++ server is still described upstream as early development. Retain Snapcast until parity is demonstrated. [Server guidance](https://www.sendspin-audio.com/build/guide/server/). |
| Files/radio | MPD (GPL) versus a player built using GStreamer (LGPL) | Keep MPD for the prototype: GStreamer is a framework, so replacing the selected player adds queue/playlist/metadata/control integration. Prefer a GStreamer solution if a concrete implementation achieves the same required formats, playlists, transport/error handling, and comparable maintenance cost. Do not commission a rewrite solely to remove GPL. Audit plugins independently. [GStreamer licensing](https://gstreamer.freedesktop.org/documentation/frequently-asked-questions/licensing.html). |
| Codecs and voice playback | Selected LGPL FFmpeg/libmpv builds | Keep the already selected lower-obligation configurations and native AAC decoding. Disabling required codecs is not an acceptable licensing shortcut; software licenses do not settle codec patents. |
| System, updates, receiver timing | Linux, BusyBox, BlueZ, NetworkManager, SWUpdate, NQPTP | Retain justified current components. No comparable lower-obligation replacement has been established by this document. A different updater must still satisfy unsigned manual A/B updates and recovery; replacing it with a signing-only design violates scope. |

Any accepted replacement updates the component register, diagrams, adapter/transport contract, recipes, and tests together. For a native Sendspin replacement, rework section 6.3's Snapcast-specific proxy/generation mechanics and prove equivalent security/ownership guarantees; do not assume the protocols are interchangeable. A successful comparison authorizes the routine substitution under AGENTS.md without another preference question. These are planned selections and integration gates, not completed migrations.

## 12. Risks and open decisions

| ID | Item | Type | Detail and proposed handling |
| --- | --- | --- | --- |
| OD-01 | Identity of the v0.0.1 reference card | **Closed 2026-11-13** | Reference hardware is Raspberry Pi 4 + HiFiBerry Amp+, confirmed by the user from the purchase invoice; the Amp+ powers the Raspberry Pi. Profile `hifiberry-amp` ("HiFiBerry Amp", Volumio naming). The roadmap's v0.0.1 gate still records the PCB revision and requires playback on the device, as for any hardware result. |
| OD-02 | Rust vs. C++ for the core | **Closed 2026-11-13** | Confirmed by the user: Rust for `nextoomio-core`, `nx-audio`, and `nx-sysd` (AD-02). The choice does not bind Matter: `nx-matter` is a separate process behind the core API and moves to the C++ reference SDK (connectedhomeip) if `rs-matter` fails spike S6 (AD-12). |
| OD-03 | Snapcast vs. Sendspin for native multiroom | Prefer lower restrictions when comparable; verify in S2/S4 | Sendspin is the preferred replacement once section 11.1 parity checks pass; Snapcast remains the current baseline. Pin exact server/client versions and audit dependencies. Preserve the ownership model, but revise and reverify protocol-specific transport/authentication/generation handling before switching. A final version number alone is not proof of parity. |
| OD-04 | Firmware `tryboot` vs. U-Boot, and final slot sizes | Lock in v0.0.1 | Spike S1 must run on a Pi 3 and a Pi 5 as well as the Pi 4, because the partition layout cannot change after the first release without breaking the upgrade path. |
| OD-05 | Native audio authentication and session binding | **Design decided; S2 verification pending** | Both channels use pinned TLS and connection-bound group-key proofs; loopback Snapcast is carried through generation-bound audio tunnels (section 6.3). No IP-only/plaintext fallback. S2 must prove isolation, authentication, and timing before v0.0.2. |
| OD-06 | AAC patent position for published binaries | Distribution assessment, not commercial-product work | The project is non-commercial; any actual redistribution obligations still need assessment under section 11, item 7. Do not add a paid certification/commercial launch workstream or silently remove AAC. |
| OD-07 | Action chains started by a local action | **Closed 2026-11-13** | Decided by the user: state caused by a local action may trigger other rules, but never a rule already in its causal chain (specification 1.11, section 9). Design in section 8.4. |
| OD-08 | Satellite volume on entering native playback | **Closed 2026-11-13** | Decided by the user: with central control enabled, a satellite entering native playback from another input or idle applies `V = G × R / 100`; resume after local pause and same-session rejoin keep the current volume (VOL-05, specification 1.11). Design in section 6.3. |
| R-01 | AirPlay 2 and Spotify Connect depend on reverse-engineered protocols | Product risk | Either can break after a vendor change. Pin versions, keep adapters isolated, and report client versions with results. |
| R-02 | Sendspin and Linux Voice Assistant are young projects | Schedule risk | Both had "technical preview"/"experimental" status during 2026. Fallbacks: Music Assistant's Snapcast provider; for voice, pin a version and carry a small patch set. |
| R-03 | AirPlay synchronization while acting as native master | Technical risk | Section 7.3. May end as a documented limitation. |
| R-04 | Wi-Fi and Bluetooth share one radio on Raspberry Pi | Technical risk | Bluetooth reception while streaming multiroom over Wi-Fi may glitch, especially on Pi 3. Wired Ethernet is the measured baseline; Wi-Fi results are reported separately as the roadmap requires. |
| R-05 | Matter controller support for speakers | Scope risk | Speaker support differs widely between ecosystems. The matrix records it; functions remain available through API, MQTT, and WebUI. |
| R-06 | Python runtime for voice increases image size and boot work | Technical risk | The voice service starts after the health check and is not part of it. |
| R-07 | Board-support layer and kernel versions for Yocto 6.0 | To verify | Confirm the Raspberry Pi layer branch, kernel version, and HiFiBerry overlays available for the pinned release before starting the image. |
| R-08 | Playback delay on a master with registered satellites | Technical risk | Section 3.2: every standalone input on such a master is delayed by the Snapcast buffer. AirPlay can compensate through its latency offset; Bluetooth cannot, so video lip-sync is lost there. If measurement shows it matters, the alternative is to use the loop only while at least one satellite is on the Master input, at the cost of a short interruption when the first one joins. |
| R-09 | Synchronization accuracy through the PipeWire graph | Technical risk | Snapcast aligns playback using the output delay it is told. Through PipeWire that delay includes graph latency and a second drift-correction loop, and it differs between boards and cards. The quantum is fixed and the per-card static latency comes from the hardware profile; spike S2 measures the real path. If the timing target fails, tune or replace the output integration while preserving software volume/mute, ownership gating, and the mandatory local Voice layer. Direct ALSA bypassing those stages is not a compliant v0.0.5 fallback; unresolved timing blocks MR-01 delivery. |
| R-10 | PipeWire access rules for ALSA/PulseAudio-compatibility clients | To verify | Section 3.2. If a client cannot be confined before linking, put its compatibility server behind an isolated audio gateway or patch the integration. Removing unauthorized links after they become audible is not an accepted fallback. Keep that input gated until S3/S5 proves preventive isolation; otherwise its requirement remains undelivered. |

## 13. Requirement traceability

| Requirement | Architecture element | Section |
| --- | --- | --- |
| SYS-01 | 64-bit image family, hardware profiles, compatibility matrix | 3.8, 9.2 |
| SYS-02 | Hardware profile per HiFiBerry card; Amp+ as the v0.0.1 reference (OD-01) | 3.8, 9.2, 12 |
| SYS-03 | Yocto 6.0 LTS with `meta-nextoomio` | 2, 5.2 |
| SYS-04 | Read-only root and boot, separate `/data` | 3.5 |
| SYS-05 | Dual boot+root slots, `tryboot`, watchdog | 3.6 |
| SYS-06 | Apache-2.0 original code, published sources and manifests | 3.9, 11 |
| SYS-07 | SWUpdate fed only by the authenticated upload; no network update client | 3.6 |
| IN-01 | `files` adapter on MPD | 5 |
| IN-02 | Kernel SMB/NFS read-only mounts + `files` adapter | 6.4 |
| IN-03 | librespot adapter | 7 |
| IN-04 | Shairport Sync + NQPTP adapter | 7 |
| IN-05 | BlueZ + PipeWire A2DP sink adapter | 7 |
| IN-06 | `radio` adapter on MPD | 5 |
| IN-07 | Sendspin player adapter `nx-ma` | 7 |
| IN-08 | `master` adapter on snapclient, master by address or name | 6.3 |
| SEL-01 | Ordered request queue; last granted start owns the device | 3.3 |
| SEL-02 | `detect_start` capability distinguishes connection from playback | 3.3, 7.3 |
| SEL-03 | Satellite commands are local sessions only | 6.3 |
| SEL-04 | `group.takeover`; Music Assistant row of the routing table | 6.3, 7.3 |
| SEL-05 | No previous-owner restoration in the state machine | 3.3 |
| SEL-06 | One session; configuration and presence are not sessions | 3.3 |
| SEL-07 | Adapters request; only the core links capture sinks | 3.2, 3.3 |
| MR-01 | One snapserver per master, synchronized snapclients | 6.3 |
| MR-02 | Master address stored on the satellite | 6.3 |
| MR-03 | Immediate unlink on stream loss; no election | 6.3 |
| MR-04 | Local session replaces Master input on that satellite only | 6.3 |
| MR-05 | `group.takeover` granted unless owner is Music Assistant | 6.3, 7.3 |
| MR-06 | No satellite command is forwarded | 6.3 |
| MR-07 | Versioned group state (volume, mute, central control) over the satellite-initiated control channel | 6.3 |
| MR-08 | Membership and registration persisted separately from sessions | 3.3, 6.3 |
| VOL-01 | Central-control flag on the master | 6.3 |
| VOL-02 | Reference `R` stored on the satellite; `V = G × R / 100` | 6.3 |
| VOL-03 | Local change writes `V` only | 6.3 |
| VOL-04 | Cached group state affects volume only with native ownership and central control enabled | 6.3, 7.3 |
| VOL-05 | Disable retains current values; enable initializes G from master volume and applies references; native entry applies G/R under central control | 6.3 |
| VOL-06 | Ducking is gain stage 2, after stage-1 volume | 3.2, 8.5 |
| TRN-01 | `transport` capability; stable rejection codes | 3.3, 5.3 |
| TRN-02 | `remote_start` capability; "waiting for client" state | 3.3, 7.3 |
| TRN-03 | `local_hold` and relink at live position | 6.3 |
| TRN-04 | Stored group mute applies only during native participation; local mute remains independent | 3.2, 6.3 |
| TRN-05 | Explicit scope on every command; limited Music Assistant transport | 3.4, 7.3 |
| REC-01 | Idle after boot; `device ready` action as the only exception | 5.3, 8.4 |
| REC-02 | Authenticated generation relink for the same logical master session with no local hold | 6.3 |
| REC-03 | Core-supervised reconnect schedule | 6.3 |
| REC-04 | Self, second-level, and cycle rejection at registration | 6.3 |
| MA-01 | Sendspin player registered with Music Assistant | 7 |
| MA-02 | Music Assistant session uses only `in.ma` | 3.2, 7.3 |
| MA-03 | Grouping done by Music Assistant; no native layer involved | 7.3 |
| MA-04 | Snapcast path independent of the Sendspin path | 6, 7 |
| MA-05 | Takeover is per device; nothing is sent to other endpoints | 7.3 |
| MA-06 | No route from `in.ma` to `mr.send` | 3.2 |
| MA-07 | Music Assistant owner leaves `mr.send` unfed | 3.2, 7.3 |
| MA-08 | New start vs. continuing stream by session ID and `detect_start` | 3.3, 7.3 |
| VOICE-01 | Linux Voice Assistant with USB microphone | 8.5 |
| VOICE-02 | Local stage-2 ducking; session untouched | 8.5 |
| VOICE-03 | `voice.out` linked only to local `out.mix` | 3.2, 8.5 |
| VOICE-04 | Voice has no session | 3.2, 8.5 |
| VOICE-05 | `mr.send` branches before gain stages and voice mix | 3.2 |
| VOICE-06 | 60 s watchdog and process-loss handling | 8.5 |
| VOICE-07 | Separate gain stages | 3.2, 8.5 |
| VOICE-08 | Interaction end changes only stage 2 | 8.5 |
| CTL-01 | REST + WebSocket API | 3.4 |
| CTL-02 | MQTT client on the command and event buses | 8.3 |
| CTL-03 | `nx-matter` and the function matrix | 9.1 |
| CTL-04 | Vue WebUI as an API client | 2, 5.3 |
| I18N-01 | Single shipped `en` catalog | 5.3 |
| I18N-02 | Key-only components, lint and CI checks | 5.3 |
| I18N-03 | ICU-style messages, `Intl`, English fallback, pseudo-locale in development | 5.3 |
| I18N-04 | Stable machine codes in API and MQTT; UI maps codes to keys | 3.4, 8.3 |
| ACT-01 | Rule model with at most 10 actions | 8.4 |
| ACT-02 | `local`, `http`, `mqtt` action types | 8.4 |
| ACT-03 | Event bus catalog | 3.4, 8.4 |
| UPD-01 | Authenticated local upload | 3.6 |
| UPD-02 | No update client in the image | 3.6 |
| UPD-03 | Signing disabled; hashes for completeness only | 3.6 |
| UPD-04 | Format, hash, hardware-compatibility, and schema validation | 3.6 |
| UPD-05 | Staging on `/data`; install on explicit command | 3.6 |
| UPD-06 | Boot-to-health 120 s budget, durable two-attempt limit, continuous watchdog handoff | 3.6 |

## 14. Sources consulted

Checked on 2026-11-13. They support the *to verify* and *decided* items above; none of them is evidence that the integration works on the target hardware.

- [Yocto Project releases](https://wiki.yoctoproject.org/wiki/Releases) and [Yocto Project 6.0 announcement](https://www.yoctoproject.org/?p=102509)
- [HiFiBerry Amp product page](https://www.hifiberry.com/products/amp/)
- [Volumio I2S DAC list (`dacs.json`, volumio3-backend)](https://raw.githubusercontent.com/volumio/volumio3-backend/master/app/plugins/system_controller/i2s_dacs/dacs.json)
- [Raspberry Pi `config.txt` / `autoboot.txt` documentation](https://raspberrypi.com/documentation/computers/config_txt.html)
- [A/B boot on Ubuntu for Raspberry Pi](https://ubuntu.com/hardware/docs/boards/explanations/piboot-ab/)
- [SWUpdate documentation](https://sbabic.github.io/swupdate) and [meta-swupdate: building with Yocto](https://sbabic.github.io/swupdate/building-with-yocto.html)
- [Snapcast](https://github.com/snapcast/snapcast)
- [Music Assistant Sendspin provider](https://beta.music-assistant.io/player-support/sendspin) and [sendspin-cpp](https://components.espressif.com/components/sendspin/sendspin-cpp/versions/0.9.4/readme?language=en)
- [Shairport Sync](https://github.com/mikebrady/shairport-sync) and its [AirPlay 2 notes](https://git.sudo.is/mirrors/shairport-sync/src/branch/master/AIRPLAY2.md)
- [librespot](https://github.com/librespot-org/librespot/wiki)
- [OHF Linux Voice Assistant](https://github.com/OHF-Voice/linux-voice-assistant)
- [rs-matter](https://github.com/asm/rs-matter)
- [Controlling a speaker via Matter and Home Assistant](https://www.matteralpha.com/how-to/how-to-control-your-smart-speaker-via-matter-and-home-assistant)

- [BlueZ Media API (optional player and media transport)](https://bluez.readthedocs.io/en/latest/media-api/)
- [Sendspin protocol: stream lifecycle and external-source handling](https://www.sendspin-audio.com/build/spec/)
- [rustls TLS exporter API](https://docs.rs/rustls/latest/rustls/struct.ConnectionCommon.html#method.export_keying_material)
