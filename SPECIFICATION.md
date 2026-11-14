# nextoomio – functional specification

Document version: **1.15**  
First prototype version: **nextoomio v0.0.1**  
Planned delivery: **v0.0.1–v0.0.5 according to [ROADMAP.md](ROADMAP.md)**  
Date: **2026-11-13**  
Status: **Functional specification for the v0.0.1–v0.0.5 prototype series. Behavior and acceptance targets are delivered incrementally according to the roadmap. Implementation and hardware measurements have not yet been verified.**  
Project name: **nextoomio**

## 1. Project goal

Create an open-source alternative to Volumio for Raspberry Pi and HiFiBerry. The device must play music independently, receive audio from common clients, support synchronized multiroom playback, and act as a Music Assistant endpoint and a Home Assistant voice satellite.

Local control, configuration, and native multiroom must not require a project cloud. Online music services may require internet access and a user account.

This document records agreed decisions and defaults for the prototype series. A requirement does not establish feasibility for every hardware combination or external ecosystem. v0.0.1 verifies basic playback on real hardware; v0.0.2–v0.0.5 incrementally deliver the complete agreed scope according to [ROADMAP.md](ROADMAP.md). Raspberry Pi 3+ and commonly used HiFiBerry cards remain the target, but each release must identify the combinations actually tested. The specification defines behavior; the roadmap defines delivery versions.

Project artifacts and application-authored text are written in English. Direct communication with the user is in Slovak, as defined in [AGENTS.md](AGENTS.md). WebUI internationalization and the initial English-only interface are specified in section 8.3.

## 2. Hardware and system

| ID | Requirement |
| --- | --- |
| SYS-01 | Target Raspberry Pi 3 and later generations (RPi 3+). Use Raspberry Pi 4 as the v0.0.1 reference board. List specific models and hardware revisions in a compatibility matrix. |
| SYS-02 | Support all commonly used HiFiBerry cards. The user-selected v0.0.1 reference card is HiFiBerry Amp+. List the specific cards and verified combinations with Raspberry Pi 3+ models and revisions in the compatibility matrix. |
| SYS-03 | Build the operating system on Yocto Linux. |
| SYS-04 | Keep the system filesystem read-only. Store writable configuration and data separately. |
| SYS-05 | Use an A/B update mechanism. |
| SYS-06 | Publish the project as open source. |
| SYS-07 | Update exclusively through manual system-image upload. Online updates and image signing/signature verification are not planned for v0.0.1–v0.0.5. |

### 2.1 v0.0.1 reference hardware

The first-release reference hardware is **Raspberry Pi 4 + HiFiBerry Amp+**. This selection is closed: the user confirmed the card from the purchase invoice on 2026-11-13 (it was earlier referred to as “Amp v1”), and the Amp+ powers the Raspberry Pi from the amplifier supply. Do not substitute the original Amp, Amp2, or another amplifier. Record the exact Raspberry Pi model/RAM and the amplifier PCB revision with the test results.

Sound-card identifiers and display names follow Volumio's I2S DAC list for Raspberry Pi wherever the card exists there, so the Amp+ is selected as “HiFiBerry Amp” (`hifiberry-amp`). The aligned HiFiBerry list is in [ARCHITECTURE.md](ARCHITECTURE.md), section 9.2.

A closed selection is not a measured result. v0.0.1 acceptance requires working playback on the actual selected combination. Later releases retain the broader RPi 3+/commonly used HiFiBerry scope.

## 3. Music sources and inputs

| ID | Requirement |
| --- | --- |
| IN-01 | Play local music files. |
| IN-02 | Play music from NAS using common protocols. |
| IN-03 | Provide a Spotify Connect receiver selectable in the Spotify application. |
| IN-04 | Provide an AirPlay receiver, with AirPlay 2 as the baseline target. AirPlay 1 is optional; a compatibility mode remains a technical decision. |
| IN-05 | Provide a Bluetooth audio receiver. Bluetooth audio transmission is not required. |
| IN-06 | Play internet radio. |
| IN-07 | Receive playback from Music Assistant. |
| IN-08 | Provide a Master input receiving native multiroom audio. Identify the source master by IP address or hostname/device name. |

The initial file-format baseline is WAV/PCM, FLAC, MP3, AAC in M4A, and Ogg Vorbis: local playback from v0.0.1 and NAS playback from v0.0.2. NAS uses SMB 2/3 or NFSv4 in read-only mode; SMB1 is not required. Internet radio from v0.0.1 supports HTTP/HTTPS MP3 or AAC streams and simple M3U/PLS playlists. Additional formats and supported profiles depend on decoder verification; listing a container does not promise every profile within it.

The prototype series uses a common internal music format of stereo PCM at 48 kHz / 24 bit. Resample other supported sample rates. Bit-perfect playback, DSD, multichannel audio, and individual internal formats per satellite are not required for v0.0.1–v0.0.5. Unsupported files or streams must produce a specific error without crashing the player.

Apple Music and SoundCloud reception is required through supported input protocols, particularly AirPlay. Native catalog browsing and direct playback through dedicated integrations are outside the agreed scope. Google Cast is not a mandatory input.

## 4. Automatic input takeover

- **SEL-01:** On each device, the last newly playing input wins: automatically switch to the input that starts playback.
- **SEL-02:** Connecting a client without playback must not take over the output.
- **SEL-03:** Changing an input on a satellite affects only that satellite.
- **SEL-04:** Changing a standalone input on the native multiroom master propagates to its group according to MR-05. Transitioning the master to Music Assistant follows MA-07.
- **SEL-05:** Ending playback does not automatically restore the previous source. Further playback requires a new playback start or an explicit command; ending Music Assistant does not restore the previous session or Master input reception.
- **SEL-06:** A device has at most one active music-playback owner: a standalone input, native multiroom, or Music Assistant. An idle device has no active owner. Stored group membership and a connected client do not constitute playback ownership.
- **SEL-07:** One shared playback-session manager per device decides takeover and audio routing. Input adapters submit requests; they must not independently seize the output or control other playback domains' groups.

For arbitration and native multiroom, standalone inputs mean local/NAS files, Spotify Connect, AirPlay, Bluetooth, and internet radio (IN-01–IN-06). The Master input and Music Assistant follow their dedicated rules below. Last-input-wins applies to a playback start addressed directly to the device; propagated group takeover is subject to the MR-05 exception.

The manager processes takeover requests serially. Concurrent requests are ordered by receipt; a monotonic sequence number resolves equal timestamps. Each session has an identifier that cannot be reused after a playback-manager restart, including a restart within the same OS boot. Transport reconnects and decoder/format changes are not, by themselves, new playback starts. Delayed audio and events from an earlier session must neither take over nor stop a newer session. On takeover, stop or disconnect the previous source according to adapter capabilities; connect new audio only after detaching the previous music path. Voice interaction uses the separate temporary layer defined in section 7.

## 5. Native multiroom

| ID | Rule |
| --- | --- |
| MR-01 | A group has one master; satellites receive its audio synchronously. |
| MR-02 | Configure a satellite's source master by IP address or hostname/device name. |
| MR-03 | A satellite using the Master input becomes silent when the master is lost. Automatic replacement-master election is not required. |
| MR-04 | A satellite can switch to its own input without affecting the master or other members. |
| MR-05 | When the master starts or changes a standalone input, it takes over its configured satellites, including satellites playing their own standalone inputs. Devices whose current playback is owned by Music Assistant are exempt and remain under Music Assistant control. |
| MR-06 | Controlling a satellite affects only that satellite. |
| MR-07 | The master controls the group. Central volume control can be enabled or disabled, and each satellite has a configurable reference volume according to section 5.1. Group mute and transport follow section 5.2. |
| MR-08 | Stored native-group membership is separate from the current playback owner. Local takeover or Music Assistant playback does not erase master configuration or satellite membership. Native-group commands must not change playback on a device currently controlled by Music Assistant. |

Devices are not equal sources whose every local input takes over the entire group. The rules above define the intended behavior.

Choose the transport and time-synchronization mechanism during technical design. Initial measurable targets and failure behavior are defined in sections 5.3 and 13.

### 5.1 Central control and reference volume

- **VOL-01:** Enable or disable central volume control on the native-group master. When enabled, the master's volume control changes group volume. When disabled, it changes only the master's local volume and leaves satellite volumes unchanged.
- **VOL-02:** Each satellite has an independently configurable, persistently stored reference volume. It defines that satellite's volume at 100% group volume. Lower group volume reduces each satellite relative to its reference; 0% group volume silences music on all controlled members. Central control does not require identical resulting volume values on every satellite.
- **VOL-03:** Changing group volume does not change stored satellite references. A local satellite-volume change affects only that satellite and does not change its reference. The next group-volume change reapplies the group value and stored reference.
- **VOL-04:** Central control applies only to members currently playing through native multiroom. Satellites playing a standalone input or Music Assistant remain unchanged. A group-volume change alone must not take over their inputs.
- **VOL-05:** Disabling central control preserves current satellite volumes and stored references. Enabling it applies the current group volume relative to each controlled satellite's reference. While central control is enabled, a satellite that enters native playback from another input or from idle, through MR-05 takeover or explicit play, also applies the current group volume relative to its reference. Resuming after a local pause and rejoining the same session after a connectivity interruption keep the satellite's current volume. While central control is disabled, entering native playback keeps the satellite's local volume.
- **VOL-06:** Apply temporary local voice ducking after calculating device music volume. It does not change group volume or satellite references.

Prototype volumes are logical values from 0–100% in 1% steps. Under central control, a satellite uses `V = G × R / 100`, where G is group volume and R is its reference; G = 50% and R = 60% yield V = 30%. The master's local output uses V = G. Logical-volume-to-gain mapping must be monotonic and shared across audio paths; 0% means digital silence. These are not acoustic-power percentages.

Central control is initially disabled, local volume defaults to 30%, and satellite references default to 100%. When enabling central control, initialize G from the master's current local volume and apply it to controlled satellites. When disabling it, the master and satellites retain their current resulting volumes. Persist references and the central-control setting. Temporary voice ducking and mute do not enter the calculation of G or R.

Acceptance scenarios:

| Scenario | Expected behavior |
| --- | --- |
| Two satellites have references of 60% and 80%; central control is enabled and group volume is 100%. | Satellite volumes are 60% and 80%. Reducing group volume lowers both relative to their references without changing the stored references. |
| Central control is disabled and the user changes master volume. | Only the master's local volume changes; satellite volumes remain unchanged. |
| One satellite plays native multiroom, another Bluetooth, and a third Music Assistant; the user changes group volume. | Apply the change only to the master and the native-multiroom satellite. Bluetooth and Music Assistant retain their volumes and inputs. |
| The user locally changes satellite volume, then changes group volume. | The local change affects neither other devices nor the reference. The group change recalculates satellite volume from group volume and its stored reference. |
| Central control is enabled with group volume 50%; a satellite with a 60% reference plays Bluetooth at 90% local volume and is taken over by the master. | The satellite starts native playback at 30%, not at 90%. Its reference is unchanged. |
| The same satellite lowers its volume locally during native playback, pauses reception, and presses play. | It rejoins at the locally set volume; the group value is reapplied only by the next group-volume change or the next entry into native playback. |

### 5.2 Play, pause, stop, and mute

- **TRN-01:** Play/pause/stop on the master control the current standalone source and therefore native distribution. Pause is available only when supported by the source. Disable unsupported commands in WebUI and reject them with a specific API error; live radio need not support pause. Stop ends the source and distribution, leaving satellites using Master silent.
- **TRN-02:** Play after pause resumes the current session. Play after stop explicitly starts the last selected source if the device can start it. An external client must not be remotely started without client support; report waiting for the client rather than pretending success.
- **TRN-03:** Pause/stop on a satellite using Master pause/end only its local reception. They do not affect the master or other satellites. Play rejoins the master's current position without playing missed audio. Pause keeps local reception paused; stop requires new play or MR-05 takeover. A new master session or input change can take over the satellite in either case according to MR-05.
- **TRN-04:** Group mute on the master is a separate native-group state, independent of central volume being enabled. Each device also has local mute. Group mute affects only current native playback, including the master's native output; stored membership alone does not make it effective. Local mute applies independently to the current music output. Leaving native playback removes the group-mute contribution without clearing its stored value. Group unmute must not clear local mute or change volumes/references. A member joining native playback inherits current group mute.
- **TRN-05:** WebUI and API distinguish group and local mute, including the master's local mute. Group commands do not affect satellites with other active inputs. Music Assistant groups are controlled in MA; nextoomio provides local volume/mute and local reception stop without stopping other endpoints. Expose additional MA transport commands only after verifying their scope.

Acceptance scenarios:

| Scenario | Expected behavior |
| --- | --- |
| The master pauses and resumes a local file. | The controlled group pauses and resumes synchronously; an MA satellite remains unaffected. |
| A satellite pauses Master reception and later presses play. | Others continue unchanged; the satellite rejoins the current position without playing the missed section. |
| One satellite has local mute; the master enables and disables group mute. | That satellite remains locally muted; other members follow their own local settings after group unmute. |
| A group-muted satellite switches to a local source or MA with local mute off, then rejoins native playback. | The new owner is not muted by the native group; rejoining inherits the still-stored group mute. |
| The master stops radio while a satellite switches to Bluetooth. | The Bluetooth satellite is neither stopped nor taken over. Others remain silent until new playback or explicit play. |

### 5.3 Failures, recovery, and startup

- **REC-01:** After reboot, restore configuration, volumes, references, and local/group mute, but do not start music by default. A startup action explicitly configured by the user may be an exception. Receivers may accept a new playback start. Activate a stored Master input through explicit play or new master takeover according to MR-05.
- **REC-02:** When master/audio connectivity is lost, discard invalid/stale audio and wait silently. If the user remains on Master and has not locally paused/stopped reception, automatically rejoin the same session's current position after recovery. This resumes an interrupted session rather than restoring an ended source under SEL-05.
- **REC-03:** Retry after 1, 2, 4, 8, then at most 10-second intervals. Switching inputs or local stop cancels automatic return. Losing one satellite must not stop the master or other satellites.
- **REC-04:** Reject the device itself as master and any detected device cycle. The prototype allows one master-to-satellite level only; a satellite must not redistribute received Master audio to another group.

## 6. Music Assistant

- **MA-01:** The device must be usable as a Music Assistant playback endpoint.
- **MA-02:** Music Assistant owns control of its playback path; the project's native master is not used for this path.
- **MA-03:** Music Assistant manages its groups without a nested native-master layer.
- **MA-04:** Native multiroom remains available for standalone operation according to section 5.
- **MA-05:** Music Assistant is another device input. Local takeover by another input must not automatically switch other Music Assistant endpoints.
- **MA-06:** Never distribute Music Assistant audio through native multiroom. Every MA-group endpoint receives playback through a Music Assistant-controlled path, without redistribution by a native master.
- **MA-07:** Music Assistant taking over a native master stops native distribution. Satellites using Master remain configured and wait silently. Satellites with standalone or Music Assistant inputs continue unchanged. MA may take over satellites separately as its own endpoints.
- **MA-08:** A new standalone playback start addressed directly to a device may take over Music Assistant. A new MA playback start addressed to it may take over again. Continuing an existing stream is not a new playback start. Other MA endpoints must not switch automatically. Format changes, decoder initialization, and reconnect/state replay from the old stream must not be interpreted as a new playback request.

Choose the endpoint protocol, discovery, and reporting of local output takeover to Music Assistant during technical design.

### 6.1 Playback-ownership acceptance scenarios

| Scenario | Expected behavior |
| --- | --- |
| A master plays radio with two satellites on Master; MA starts only on the master. | The master plays MA and stops native distribution. Both satellites wait silently; group configuration remains stored. |
| MA starts on the master and two satellites as its own group. | Each endpoint plays through an MA-controlled path. Native multiroom does not carry this audio. |
| A satellite starts receiving Bluetooth during native multiroom playback. | Only that satellite switches to Bluetooth; the master and others continue. |
| The master changes radio to AirPlay; one satellite plays Bluetooth and another MA. | The master and Bluetooth satellite switch to native multiroom carrying AirPlay. The MA satellite continues unchanged. |
| An MA device receives a new standalone input while the original MA stream continues arriving. | The new input takes over. The continuing MA stream does not take it back; a new MA playback start may. Other MA endpoints continue unchanged. |
| MA ends on a device that previously played another source. | The device remains idle without restoring the previous source. |
| After MA ends, the master starts radio. | Resume native distribution and take over configured satellites under MR-05, leaving devices still owned by MA unaffected. |

## 7. Home Assistant Voice

- **VOICE-01:** With a USB microphone connected, the device can operate as a Home Assistant Voice satellite.
- **VOICE-02:** Detecting the wake word temporarily ducks music only on the device's local output. Music continues without pause, preserving synchronization with other native-multiroom or MA endpoints.
- **VOICE-03:** Capture speech and play the assistant response only on the device where the interaction was activated. Do not distribute the response to the native group or other MA endpoints.
- **VOICE-04:** Voice interaction is a temporary local audio layer over music playback. Wake-word detection, capture, and response playback do not change the active music input, playback owner, group membership, or group control. The response does not participate in last-input-wins arbitration.
- **VOICE-05:** On a master, apply local ducking and mix the response after the native-distribution branch. Satellites continue receiving the original music stream without ducking or response audio. The same local isolation applies during MA playback.
- **VOICE-06:** Remove temporary ducking on completion, cancellation, error, or timeout. Assistant errors or missing responses must not leave music permanently ducked.
- **VOICE-07:** Apply ducking separately from user music-volume and mute settings. Removing it uses current settings; changes made during interaction must not be overwritten with earlier values.
- **VOICE-08:** Finishing an interaction must not start stopped music or restore an earlier input/owner. If music playback changes during interaction, retain the current state when ducking ends.

An explicit user command to control music through the assistant follows normal playback/group rules. These restrictions concern the voice interaction and its audio layer themselves.

Default music ducking is −20 dB with approximately 200 ms attack/release, applied locally only. The entire interaction, including the response, has a fixed 60-second prototype limit, after which it ends and ducking is removed. Choose wake-word detection, voice transport, microphone selection, and echo cancellation during technical design. Speech recognition and response generation need not run on the RPi itself.

### 7.1 Voice-interaction acceptance scenarios

| Scenario | Expected behavior |
| --- | --- |
| A master and two satellites play radio; voice activates on the master. | Only the master ducks music and plays the response locally. Satellites receive original music without the response. The master removes ducking afterward and remains synchronized. |
| Voice activates on a satellite receiving Master. | Only that satellite ducks music and responds; Master remains selected. The master and other satellites continue unchanged. |
| Voice activates on a device in an MA group. | The local voice layer does not take music ownership, change the group, or affect other endpoints. Music remains synchronized. |
| The user changes music volume or mute during interaction. | The new settings remain afterward; do not restore the old volume or mute. |
| The user stops music during interaction. | Completing the response or removing ducking does not restart music. |
| A new input takes over music playback during interaction under arbitration rules. | The new input/owner remain active afterward; do not restore the old source. |
| Interaction is canceled, the assistant fails, or no response arrives before timeout. | Remove ducking and retain current music state and user settings. |

## 8. Control and state

| ID | Interface | Scope |
| --- | --- | --- |
| CTL-01 | Local API | Programmatic device control and state access. |
| CTL-02 | MQTT | Commands, state publication, and events. |
| CTL-03 | Matter | The maximum technically feasible scope of standard control and state reporting, without certification being required. |
| CTL-04 | Basic WebUI | Local user control and configuration. |

### 8.1 Matter

Expose all functions that can be correctly represented by standard Matter clusters. Determine the concrete scope through function mapping and verification in target ecosystems; equal functionality in every controller is not promised without testing.

Functions not covered by Matter remain available through the local API, MQTT, and WebUI. The device is a controlled device, not a Matter controller or a bridge for other devices. Where group mute is represented, it remains usable when central volume is disabled; changing volume policy must not remove that control. Unsupported/disabled level commands must neither enable central volume nor change mute as a side effect.

### 8.2 Basic WebUI

The basic interface includes:

- Current input, playback state, and available metadata.
- Play/pause/stop according to input capabilities, volume, and mute.
- Input, radio, and local/NAS file selection.
- Master-input configuration and multiroom status.
- Central-volume enable/disable and reference-volume settings per satellite.
- Network, audio, MQTT, and follow-up-action settings.
- Device state, manual update-image upload, update progress, and basic diagnostics.

Detailed screens follow during design. Each feature's controls are delivered with that feature according to the roadmap.

### 8.3 WebUI language and internationalization

- **I18N-01:** English (`en`) is the only initially supported WebUI language and its default. No additional language or language selector is required in v0.0.1–v0.0.5.
- **I18N-02:** Store all application-authored user-facing text in separate translation catalogs addressed through stable keys. Include navigation, labels, buttons, validation, errors, notifications, tooltips, and accessibility text. Do not hardcode copy in components or business logic; adding a language must require a new catalog rather than rewriting those layers.
- **I18N-03:** Support parameter interpolation and plural-aware messages, avoid concatenating translated sentence fragments, and fall back to English when a future catalog lacks a key. Use locale-aware formatting for dates, times, and numbers where displayed. A missing key must not expose an internal identifier to the user; detect catalog gaps during development/verification.
- **I18N-04:** Keep API/MQTT identifiers, enum values, and machine-readable error codes stable and independent of presentation language. Map application errors to catalog messages in the UI. Do not automatically translate media metadata, station/file names, device names, or other user-provided content.

Establish catalog-based copy with the first WebUI in v0.0.1. Every later screen follows the same convention; v0.0.5 verifies complete coverage. Acceptance includes English-only onboarding and normal/error states, catalog-key coverage, and a development-only sample catalog proving that another language can be added without changing components or business logic. The sample is not shipped as another supported language.

## 9. Events and follow-up actions

- **ACT-01:** Assign at most **10 follow-up actions** to an event.
- **ACT-02:** Support local actions, web requests, and MQTT messages, similar to Shelly Actions.
- **ACT-03:** Cover common device and playback events.

The initial event catalog covers device ready, playback started/paused/stopped, input/volume/mute changed, master available/unavailable, and update succeeded/failed. Define MQTT topics and exact payloads during technical design. A change event occurs only when a value actually changes, not when the same value is written again.

Example: playback starting triggers an HTTP or MQTT action to switch on an external amplifier.

From action delivery in v0.0.4, apply these rules:

- Execute one event's actions sequentially in stored order. Each may have a 0–60-second delay; execution timeout is 3 seconds. Execution must not block audio or control.
- HTTP actions support GET/POST with configured URL, headers, and body. MQTT actions publish to a configured topic with QoS 0 and no retain. Local actions use a fixed allowlist: input selection, play/pause/stop, volume, and mute. Arbitrary shell commands are outside the prototype scope.
- Log failures and continue to the next action. Automatic retries, conditions, scripts, and durable delivery across reboot are not implemented in the prototype series.
- Publish state/events caused by a local action. They may trigger other rules, but never a rule that is already part of the chain that caused them: a rule cannot re-trigger itself, directly or through other rules. For example, a startup rule that starts playback does trigger the playback-started rule that switches on an external amplifier. Do not run the same rule concurrently. Disable a rule with a diagnostic error if it attempts to start more than 20 times within 10 seconds, including HTTP/MQTT feedback.
- Queue at most 20 pending events for actions. Log overflow and drop additional events for action execution; current-state publication and music continue. Cancel pending actions when the process exits.

## 10. Security, data, and updates

The following minimum requirements apply to v0.0.1–v0.0.5 on a local network. Deliver each protection with the feature it protects: API authentication in v0.0.1, the multiroom key in v0.0.2, and MQTT TLS in v0.0.4, for example.

- One administrator account configured at first startup, without a universal default password. Access before setup completion is limited to initial setup.
- Authenticated WebUI and local API, including image upload; CSRF protection for browser sessions and input validation.
- HTTPS for WebUI/API using a local certificate; MQTT TLS with certificate verification. Unencrypted MQTT may be explicitly configured for a local broker.
- Explicit Bluetooth pairing and a shared random native-group access key configured on the master and its satellites. Authenticate both native control and audio peers; an allowed IP address alone is insufficient. Pairing may package the key together with the master identity for offline verification.
- Salted administrator-password hashes; other secrets in persistent storage accessible only to necessary services and excluded from ordinary logs.
- A/B updates and automatic return after failed boot or health checks.
- Configuration and user-data preservation during updates and return to the previous slot.
- Least-privilege services and removal/limitation of unnecessary network services. Shell access (SSH) is off unless enabled through the storage card as described in section 10.1.
- Controlled execution of local actions and protection against loops.
- Safe initial setup, configuration backup, and restore.

Operate the prototype on a local network without direct internet exposure. Choose authentication implementation, certificates, partition layout, and update tooling during technical design. Verify rollback data preservation including schema compatibility: a new version must not irreversibly overwrite configuration needed by the previous slot.

### 10.1 Manual updates for v0.0.1–v0.0.5

SSH access is disabled by default. It is enabled only by placing a public-key file on the first FAT partition of the storage card, uses key authentication only, and is disabled again by removing the file. Enabling it therefore requires physical possession of the card; it cannot be enabled through the WebUI, API, MQTT, or Matter. While it is enabled, a developer may start an image upload from a workstation over SSH and may require a confirmation from that workstation before a trial slot is committed. This path uses the same A/B mechanism, validation, and rollback rules as below and is not an online update. The WebUI and diagnostics show whether SSH access is enabled.

- **UPD-01:** Start updates through manual system-image upload in the local WebUI. Device internet access is not required.
- **UPD-02:** Do not discover, download, or automatically install online updates. A manually uploaded image is the only update input.
- **UPD-03:** Do not implement image signing or cryptographic signature verification in v0.0.1–v0.0.5. An image signature is not an acceptance prerequisite.
- **UPD-04:** Install manually uploaded images through SYS-05 A/B updates. Before activation, validate format, completeness, and target-device compatibility. These checks do not verify signatures or image origin. Define the image format and validation procedure during technical design.
- **UPD-05:** Stage and validate uploads outside the active system slot. Invalid/incomplete images or insufficient space must not change the boot slot. Once installation is explicitly started, stop playback and write the inactive slot; upload alone must not stop playback.
- **UPD-06:** Mark the new slot healthy only after local control, API, and the configured audio output start successfully. Each trial boot has 120 seconds to become healthy; exceeding the limit or crashing is a failed attempt. Allow at most two attempts, then use the previous slot on the next boot. Internet, NAS, MA, or HA unavailability alone must not trigger rollback. Ensure reboot if a trial boot hangs, including before the application or systemd starts. The 120-second trial budget must not restart when a later boot stage starts, and consumed attempts must survive process restart or power loss.

## 11. Open source and licensing

Publish the project as open source. The provisional license for original code is **Apache License 2.0 (Apache-2.0)**; confirm it and compatibility with selected components before publication.

The project is intended for non-commercial open-source use, not as a commercial product. This intent does not impose a non-commercial-only license on the original code. For technically comparable alternatives, prefer the component with fewer licensing and redistribution restrictions, considering functionality, reliability, security, maintenance, hardware performance, and integration effort. GPL/LGPL remain acceptable when a suitable equivalent is not established. Preserve all agreed functionality and comply with redistribution obligations even when releases are free. Apply the selection rule in [AGENTS.md](AGENTS.md) to code, model weights, firmware, assets, and dependencies separately.

The earlier discussion's reference to “Apache 3” was corrected to Apache-2.0. External components retain their own licenses. Do not assign the entire Yocto image the original application's license without assessing its components.

## 12. Outside the current scope

- Zigbee, originally mentioned instead of MQTT by mistake.
- A separate direct Home Assistant player integration; Music Assistant and HA Voice remain separately required.
- Bluetooth audio transmission.
- Matter certification.
- Multiple masters in one native group and automatic master failover.
- Dedicated native catalog integrations for Apple Music, YouTube Music, and SoundCloud.
- Online update discovery, download, and automatic installation in v0.0.1–v0.0.5.
- Image signing and cryptographic signature verification in v0.0.1–v0.0.5.

## 13. Delivery of the v0.0.1–v0.0.5 prototype series

### 13.1 Release allocation

The full functionality, dependencies, release gates, and requirement-ID coverage are defined in [ROADMAP.md](ROADMAP.md).

| Version | New functionality |
| --- | --- |
| Phase 0 (v0.0.0, internal) | No user functionality. A/B base image for the reference hardware that is installed and replaced remotely, with automatic return from a bad image, so later releases can be tested without physical access to the device. Not a public release. |
| v0.0.1 | Standalone Yocto player targeting Raspberry Pi 4 + HiFiBerry Amp+: local files, radio, API, English-only translation-ready WebUI, safe initial setup, manual A/B updates, and configuration backup/restore. Reference hardware is closed under section 2.1. |
| v0.0.2 | NAS, Wi-Fi configuration, and native multiroom with central volume, satellite references, group control, and failure recovery. |
| v0.0.3 | AirPlay 2, Spotify Connect, Bluetooth reception, and Music Assistant with complete input arbitration and group isolation. |
| v0.0.4 | MQTT, events, up to 10 follow-up actions, and Home Assistant Voice with a local voice layer. |
| v0.0.5 | Matter in its verified feasible scope, complete RPi 3+/common HiFiBerry support, complete translation-ready English WebUI, and integrated verification of all agreed functionality. |

Versions are cumulative. Moving a feature requires updating the roadmap and coverage matrix and must not silently remove it from v0.0.5. Mark a feature delivered only after successful verification. This is a delivery plan, not a claim of existing implementation.

### 13.2 Initial acceptance targets

Numbers are targets for initial measurement, not established properties. Report results with hardware, topology, and image version; a missed target must not be reported as a passing test.

| Area | Target and verification from the relevant feature's delivery version |
| --- | --- |
| Startup | Local WebUI/API available within 90 seconds of power-on on the reference Ethernet/DHCP setup. No automatic music playback with default configuration. |
| Multiroom size | At most 4 devices: 1 master and 3 satellites. Test first on 2 devices, then all 4. |
| Synchronization | At most 2 ms between identical audio-impulse onsets at electrical outputs after settling. Measure at least two outputs together and check drift for 30 minutes. Establish the wired-network baseline; evaluate Wi-Fi separately. |
| Group join | Reach the master's current position and synchronization target within 5 seconds of successful connection. |
| Failure recovery | Silence within 2 seconds of stream loss without replaying old buffers; resume the active session within 15 seconds after stable connectivity and master availability return. |
| Control | Acknowledge an ordinary local API command within 300 ms; longer operations expose progress/state. Available local files or direct radio streams start within 5 seconds. |
| Basic stability | At least 2 hours of continuous reference-setup playback without a service crash or spontaneous input switch. |
| Voice layer | Pass section 7.1, including errors/timeouts; no assistant response or temporary ducking on another endpoint. |
| Updates | Reject damaged/wrong-target images; complete upload/install; verify rollback of an unhealthy image while preserving configuration and references. |
| Control isolation | Local satellite actions and group-volume/mute changes do not affect MA endpoints. Include delayed events from old sessions. |
| WebUI language | English-only application copy, valid catalog keys, translated presentation of application errors, and a development-only additional catalog requiring no component/business-logic changes. |

### 13.3 Technical implementation deliverables

- Selected audio/integration components, versions, licenses, and verification results. Do not treat the resulting image as one package under the original-code license.
- Audio pipeline including the multiroom branch before local volume/mute and Voice, session states, authenticated transport-generation binding, and cancellation of stale requests. Bluetooth reception must work without optional AVRCP player status; actual media delivery, including silent frames, distinguishes playback from connection.
- Concrete API, MQTT topics/payloads, authentication, and errors. WebUI and MQTT use the same command/state model as the local API.
- English translation catalog, stable keys, interpolation/plural/formatting conventions, missing-key verification, and instructions for adding a language without changing components.
- Yocto layers, image format, partition layout, trial-boot health checks, rollback-compatible configuration, and backup/restore procedure.
- Acceptance results and specific defects/limitations for the next release. At v0.0.5, unresolved limitations must not substitute for mandatory agreed features. This document is a design; without results it is not a completed implementation.

## 14. Reference material

These links inform subsequent technical design and do not mandate a component choice:

- [Apache licenses](https://www.apache.org/licenses/)
- [Apache licensing FAQ](https://www.apache.org/foundation/license-faq.html)
- [Shairport Sync – AirPlay receiver](https://github.com/mikebrady/shairport-sync)
- [Shairport Sync configuration and compatibility mode](https://github.com/mikebrady/shairport-sync/blob/master/scripts/shairport-sync.conf)
- [librespot – Spotify client](https://github.com/librespot-org/librespot)
- [Music Assistant developer documentation](https://developers.music-assistant.io/)
