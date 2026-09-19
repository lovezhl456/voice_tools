# SIP tool: Agent and maintainer contract

Contract version: 1.0. Human guide: [sip.md](sip.md). Acceptance evidence: [sip-validation.md](sip-validation.md). Entry points: `voice-tools sip` and `voice-sip`. Discover current CLI arguments with `voice-tools schema --tool sip`; runtime schema is authoritative.

Portable historical acceptance: [HTML case catalog](sip-cases/index.html), [JSON summary](sip-cases/results.json). The committed summary preserves case steps, assertions, metrics, timestamps and source hashes; full recordings and raw logs remain in the original local evidence package. Regenerate the HTML with `python scripts/sip_case_catalog.py`.

## Scope and invocation

Lightweight PJSUA2/Python single-call automation over SIP UDP. No FreeSWITCH/ESL execution service. User-selected SIP gateways may be OpenSIPS or FreeSWITCH. Current acceptance includes localhost and the explicitly authorized public test-service phase described below; user gateway configuration is pending.

Target selection background (2026-09-19): [sip-test-targets.md](sip-test-targets.md). Its historical research-only notes are superseded by the execution evidence below. SIP5060 UDP suitability remains unconfirmed (published routing requires TLS). For local SIPp v3.7.7 built-in UAS, use PCMU; default SDP lacks telephone-event. RTP echo uses ordinary UDP, unlike raw-socket PCAP replay. pjsua shares the caller's PJSIP stack; Baresip provides an independent stack but its echo module is experimental.

Follow-on execution supersedes the research-only status: [sip-interop.md](sip-interop.md), 2026-09-19, user explicitly authorized pjsua → Baresip → public tests. Final 20/20 E2E cases passed (9/7/4); 6/6 selected Baresip upstream selftests passed. Public SIP2SIP 4444/3333 and IPTel echo/music calls used current UDP SRV-discovered proxies, synthetic media, no configured authentication or registration. Do not generalize this observation into perpetual anonymous access or arbitrary public calling scope. Runner: `python -m tests.sip.interop --phase pjsua|baresip|public --out NEW_DIR`; `--case ID` selects cases. Public phase sends real calls and is opt-in; normal unittest discovery sends none. Override binary/prefix via `VOICE_SIP_PJSUA` and `VOICE_SIP_BARESIP_PREFIX` when needed. Report machine data: `outputs/sip-interop-report-20260919/results.json`, file hashes in `manifest.json`, prior failed attempts retained. Cases P09/B07 cover PCAP conversion only, not SIPp raw replay. Audio assertions use ten ordered 100 ms windows, at least 8 scores >=0.65, median >=0.85 and offset spread <=120 ms; they are not MOS/PESQ. Negative SIP cases pass only when the expected failure and cleanup occur. This phase changed test tooling/config/docs, not production CLI code.

Invoke subprocesses with argument arrays. `voice-tools --json sip ACTION ...` and `voice-sip --json ACTION ...` return the existing JSON envelope. Native PJSUA2 and SIPp run in child processes; their output goes into artifacts, not parent stdout.

| Action | Network / process effects | Output |
|---|---|---|
| `doctor` | Local native import probe; no SIP | Dependency availability; exit 3 when PJSUA2 unusable |
| `init --out DIR` | Local writes | Placeholder scenario |
| `validate SCENARIO` | Local reads; no native import | Fully resolved validated plan |
| `run SCENARIO --dry-run --out DIR` | Local reads/writes; no PJSUA2 import or network | plan.json/result.json |
| `run SCENARIO --out DIR` | One SIP call to configured endpoint, optional configured registration/proxy | Call events, RX recording, source timeline, result |
| `pcap-inspect PCAP` | Offline tshark | Explicit single-direction stream IDs |
| `pcap-import PCAP --stream ID --out DIR` | Offline tshark + G.711 decode | media.json, audio.wav, dtmf.json, scenario.json |
| `sipp-prepare ... --out DIR` | Offline tshark | Filtered selected.pcap, scenario.xml, plan.json |
| `sipp-run PACKAGE --dry-run --out DIR` | Local validation/writes only | Command plan |
| `sipp-run PACKAGE --out DIR` | One independent SIPp call; optional local capture | Result/logs, optional media.pcapng |

Do not invent endpoints, passwords, codec mappings or stream direction. Use already-authorized task scope; do not add repeated confirmations. Missing gateway input does not block offline work or specifically authorized localhost tests. Outputs must be new/empty directories; never erase existing recordings to satisfy this requirement.

## Scenario schema 1.0

JSON only. Unknown keys, nonfinite numbers and boolean numeric values are rejected. Paths resolve relative to the scenario/media manifest, not the caller's working directory.

| Field | Type/default/bounds |
|---|---|
| `schema_version` | Required string `1.0` |
| `target_uri` | Required `sip:user@host[:port][;transport=udp]`; no CRLF/display names/TCP/TLS |
| `account.id_uri` | Default `sip:voice-tools@127.0.0.1` |
| `account.registrar_uri` | Optional `sip:host[:port]`; omit to skip REGISTER |
| `account.proxy_uri` | Optional outbound proxy URI |
| `account.auth` | Object with username, optional realm (`*`), password_env; never plaintext password |
| `network.bind_address` | Optional local IPv4 for SIP and RTP |
| `network.public_address` | Optional advertised IPv4; does not configure NAT |
| `network.sip_port` | Integer 0–65535, default 0 (ephemeral) |
| `network.rtp_port` | Even integer 1024–65000, default 4000; adjacent RTCP port reserved |
| `codec` | `PCMA` (default) or `PCMU` |
| `connect_timeout_s` | Number 1–120, default 30; also bounds registration waiting |
| `max_call_s` | Number 1–900, default 120; strategy duration must fit |
| `record_early` | Boolean, default false |
| `steps` | Required list of 1–256 supported actions |

Supported actions:

```json
[
  {"action": "wait", "seconds": 1},
  {"action": "play", "file": "question.wav"},
  {"action": "dtmf", "digits": "12#", "method": "rfc4733", "duration_ms": 160, "gap_ms": 100},
  {"action": "play_media", "file": "media.json"},
  {"action": "hangup"}
]
```

`wait.seconds`: 0–900. `play`: only 8 kHz mono PCM16 WAV, duration >0 and <=900 s. `dtmf.digits`: 1–128 of `0123456789*#ABCD`; method `rfc4733` default or `sip_info`; duration 40–2000 ms default 160; gap 40–2000 ms default 100. `hangup` must be last if present; cleanup hangs up even when omitted or a step fails. `play_media` validates manifest hash and runs file playback plus timed DTMF on the same media-start clock.

Use explicit account/network values from the operator. A successful registration is not a successful call. A command accepted by the SIP library is not remote business success. Unknown action names never silently become waits.

## Media and PCAP contract

Use `pcap-inspect` before selecting `--stream`. IDs identify src IP/port, dst IP/port and SSRC. Never replay both sides of the original call. Stream selection is mandatory even when only one candidate exists.

- PCAP/PCAPNG parsing uses optional local tshark. Explicit `--rtp-port` supplies Decode As only for observed RTP ports.
- Input <=128 MiB, <=100000 capture packets, <=128 RTP streams. Limit exceeded means failure, not a silently truncated success.
- Decode PCMA/PCMU only. Static PT 8/0 can be inferred; dynamic G.711 requires codec/audio PT. DTMF PT is always explicitly provided when present.
- No auto SRTP decryption, SIP INFO conversion, CN codec conversion, mixed-SSRC merge or arbitrary compressed-codec decoding.
- DTMF event identity is RTP timestamp + event code within selected SSRC. Repeated update/end packets merge; duration is maximum observed. Missing end is reported. Unsupported events, conflict/overlap, timestamp resets beyond bounds and truncated selected packets fail.
- RTP timestamp wrap and packet reordering are handled. Audio gaps fill with zero and count in source metadata. Time zero is earliest selected RTP timestamp, not INVITE/200 OK or capture start.

`media.json` schema 1.0 fields: `audio` relative path, `audio_sha256`, `duration_s`, `dtmf`, `source`, `warnings`. DTMF entries: `at_s`, `digit`, integer `duration_ms` (40–8000), `end_observed`. Entries must be ordered, nonoverlapping and fit the audio duration. Editing WAV invalidates the manifest. Do not replace the hash to conceal changed evidence.

`dtmf.json` is an inspection artifact; execution reads DTMF in `media.json`. Do not edit the former expecting behavior to change.

## Runtime / artifact semantics

`result.json.status`: `planned`, `completed`, `failed`, or `interrupted`. `business_assertions=not_evaluated` deliberately separates procedure completion from IVR semantic correctness.

- `rx.wav`: actual received decoded audio, including PJSUA jitter-buffer effects. Early media only when enabled. Recording is continuous during local playback and waits.
- `tx_source.wav`: reconstruction of local source files using actual playback-start/stop scheduler events. NOT captured RTP, NOT guaranteed remotely delivered, NOT sample-synchronized duplex recording. No fabricated second channel. Out-of-band DTMF is in events, not inserted as tones.
- `events.jsonl.at_s`: monotonic seconds relative to run_start. `recording.starts_at_run_s` locates recording origin approximately. Do not mix this clock directly with RTP ticks or unrelated QA event schemas.
- Call-ID, final observed SIP status/reason and negotiated codec are separate from action count. Preserve failures and logs.
- Credentials only resolve in the execution worker from `password_env`. Plans store the variable name. Native SIP message logging is off; do not enable verbose logs on credentialed traffic without considering their contents.
- Before a real call, playback WAVs are copied into output/sources with checked SHA256. Resolved plan audio.file points to the snapshot; audio.source_file preserves the original path. Both native playback and TX-source reconstruction use these same bytes. Dry-run does not copy audio.
- Active SDP media changes reconnect both the existing recorder and player; they do not restart the recording file. Inactive media clears readiness. Call deadlines continue to run during media changes.
- CLI SIGINT/SIGTERM closes the worker and preserves an interrupted receipt; SIGKILL cannot be handled. Signal handlers are scoped and restored, and only installed on the main Python thread. A worker execution timeout is `failed / WORKER_TIMEOUT`, not operator interruption.
- Malformed worker receipts are preserved as `worker-result.invalid.json` and reported as `WORKER_RESULT_INVALID`. RX success requires complete mono 8 kHz PCM16 data, not just a nonempty WAV header. `secondary_errors` retains cleanup failures without replacing the first call error.

Exit 0 = operation/plan completed, not audio/IVR correctness. Exit 2 = invalid input/config/missing runtime dependency. Exit 3 = incomplete/failed call, replay, recording or unusable PJSUA2 in doctor. Existing envelope marks exit 3 `partial_error`; inspect `summary.status`, `summary.error` and result.json for the specific outcome. Never retry automatically merely because a call returned nonzero.

## SIPp path

`sipp-prepare` creates a one-call IPv4 G.711 UAC scenario with original RTP payloads. No REGISTER/Digest support here. SDP mapping must match the original codec and telephone-event PT; no packet transcode/PT rewrite. PCAP playback is asynchronous, so the generated scenario waits for capture duration before BYE.

Package hashes are checked before running. Only the generated `exec play_pcap_audio=selected.pcap` action is accepted; arbitrary SIPp shell exec actions are refused. Runner uses argv lists, bounded timeout and process cleanup. This is an independent call, never a second media injector into a running PJSUA2 call.

No automatic received WAV. `--capture-interface` starts tshark first and writes media.pcapng. Without it, report `capture_enabled=false`. Capture startup failure prevents dialing; runtime capture failure prevents a success result. No automatic privilege escalation or system configuration changes.

## Maintainer map and verification

`tools/sip/cli.py` defines discoverable arguments; `scenario.py` validates offline; `pcap.py` extracts/decodes; `runner.py` owns the bounded call state machine; `pjsua.py` adapts native APIs; `worker.py` isolates native output/crashes; `service.py` orchestrates workers; `sipp.py` prepares/runs independent replay packages.

`processes.py` provides scoped termination handling. Post-merge audit and added failure cases: [sip-deep-review.md](sip-deep-review.md). PCAP export and media loading share the same DTMF limits: at most 256 ordered nonoverlapping events, 40–8000 ms each, optional `end_observed` must be boolean.

No tool-to-tool imports. Shared helpers come from `core`. Keep optional imports out of registration/help/schema. Python 3.9+; no audioop dependency, GPU, model or service framework. State changes should keep receive recording active and release native objects before destroying the endpoint. Callback EOF only signals state; do not destroy players from media callbacks.

Run relevant tests under `tests/sip`; optional actual localhost tests use `VOICE_TOOLS_SIP_LOOPBACK=1`. The independent peer records observed UDP audio/DTMF and validates Digest, so native tests are not SDK mocks. Keep new failure behavior covered, update both human/Agent docs and examples, regenerate CLI schema from actual parser once, then run the full repository suite. Preserve unrelated uncommitted tool work.

Do not claim gateway/NAT, large concurrency, arbitrary codecs, unimplemented ASR branches or portable native wheels are validated by local synthetic tests.
