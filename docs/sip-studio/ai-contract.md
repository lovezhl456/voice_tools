# SIP Case Studio — AI / maintainer contract

Contract version: 1.0. Scope: static local editor prototype. Human guide: [README.md](README.md). Product decisions: [design.md](design.md). Runtime authority: [../sip-ai.md](../sip-ai.md), actual CLI schema and `src/voice_tools/tools/sip/scenario.py`.

## Workflow

Before each new work session, fetch the latest remote main and **merge origin/main into the active work branch**. Preserve unrelated changes; do not reset or discard them. Resolve conflicts before continuing. This is the user's explicit workflow instruction. Before every PR submission, perform a deep review of the final diff, boundaries, failure paths, regression risks, documentation and evidence; fix findings and rerun the affected checks. Report unresolved failures honestly. This is also an explicit user instruction. Record the merge baseline in acceptance notes. Current check baseline: `6849b18c9765ede4287212f73a8a2e15928791b4`, fetched and merged 2026-09-19.

Do not use this prototype delivery as authorization to place calls, create bulk campaigns, install a SIP server or publish a cloud site. Local HTML remains the default. The existing Python/PJSUA2 CLI owns runtime semantics; no FreeSWITCH/ESL execution service is introduced. Existing OpenSIPS/FreeSWITCH gateways can still be targets.

## Module boundaries

- `core.js`: pure UMD browser/Node compiler, schema checks, immutable reorder, WAV parser, media manifest field validator. No DOM, network, SIP, filesystem or shell.
- `app.js`: local UI state, drag/drop and keyboard alternatives, forms, localStorage, safe text rendering, File/object URL preview, explicit downloads. No external dependencies or remote requests.
- `styles.css`, `index.html`: desktop sequential editor and mobile stacked layout.
- `sources.json`: primary-source evidence metadata, pinned repository commits and content hashes. Private raw research snapshots stay under ignored `.local/sip-studio-research/`.
- `tests/studio/core.test.cjs`: meaningful edge/semantic checks.
- `tests/studio/check_cli_compat.py`: generated JS export → actual Python validate/dry-run, synthetic WAV/media in temp directories. Never calls SIP.

## Data contracts

Studio document: `studio_version`, title, tags, environment `{name,config}`, steps with local id/label and runtime action params. Current version `1.0`. Import creates new IDs and adds a new case/environment; do not overwrite another case. CLI import is also supported, filling **CLI defaults**, not changing omitted behavior such as implicitly appending hangup.

Runtime export: only the existing strict scenario 1.0 fields. Use an explicit action/field whitelist. Never serialize IDs, labels, positions, tags, plugin metadata or a proposed capability into CLI output. Unknown fields/actions, plaintext password fields, malformed JSON and unsupported versions must fail import before mutating state.

Supported actions: wait/play/dtmf/play_media/hangup. DTMF budget includes a trailing gap for every digit, matching CLI. One final hangup may be added by UI, but imported files may omit it. Keep browser boundary checks aligned with latest main, especially null vs missing, boolean numeric input, DTMF end_observed and floating-point event edges. Authoritative disk validation is always CLI validate.

Authentication: username/realm/**password_env** only. UI never asks for or stores the secret value. No request URLs or arbitrary commands in plugin config. A future backend uses subprocess argv arrays and controlled workspace paths, not shell concatenation.

## Truthfulness requirements

- Offline preview computes known step offsets, not actual events, audio playback, call success, or expected IVR response. Unknown duration makes subsequent absolute offsets unknown.
- Real run history stays empty until real artifacts are connected. Separate procedure completion, media checks and business assertions. `not_evaluated` never renders as pass.
- RX recording is automatic at call level; `record_early` is a setting. Do not invent record start/stop actions. `tx_source.wav` is local source reconstruction, not captured transmitted RTP or remote reception proof.
- Browser file selection does not install, upload, copy or transcode assets. WAV preview validates the selected bytes; current-session object URLs expire on reload. Paths and metadata persist separately. UI must keep the need for actual CLI path/hash validation visible.
- media.json parsing only checks fields/events. It cannot verify referenced audio bytes or hash. PCAP remains inspect → explicit single direction/SSRC → import in CLI. Raw RTP replay belongs to separate SIPp execution.
- Do not reuse historical 20/20 CLI acceptance as proof of GUI execution. Latest main's deep-review report retains Baresip 2/7 with unresolved waveform failures. This UI task performs no new calls.

## State and recovery

localStorage key: `voice-tools.sip-studio.v1`. Save valid drafts only; when fields are invalid, preserve the previous valid stored snapshot and visibly mark pending edits. On unavailable storage, keep the live session and recommend export. On corrupt stored content, preserve raw text and offer recovery download; do not silently erase it. Limit cases/assets to 100; JSON to 1 MiB; WAV preview to 16 MiB; undo snapshots to 30; session-only previews to 30.

DOM text and attributes derived from user input must be escaped; previews must not interpret HTML or execute arbitrary node code. Keep the prototype offline and free of CDN dependencies. Blob audio URLs must come only from user-selected files.

## Verification and documentation

Run `node --test tests/studio/core.test.cjs` and `PYTHONPATH=src python3 tests/studio/check_cli_compat.py`. Browser-test desktop/mobile drag + click/up/down equivalents, continuous form editing, export/import download artifacts, invalid JSON preserving old cases, WAV file checks, reload recovery, modal dismiss/focus, logs and resource HTTPs. Update [acceptance.md](acceptance.md) with actual evidence and limitations. No full native SIP suite is required for UI-only changes unless runtime code changes.

Maintain both human README/design and this AI contract. Link the local UI from the existing voice-tools navigation; preserve other cards. `docs/sip-studio/` is the source; the deployment symlink points here. Avoid writing shared global web-server configuration.
