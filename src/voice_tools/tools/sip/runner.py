"""A bounded single-call state machine, independent of PJSUA2 for testing."""
import array
import json
import sys
import time
import wave
from pathlib import Path

from voice_tools.core.files import write_json
from .scenario import VERSION


class CallFailure(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class ExpectedRejection(Exception):
    """A configured negative SIP test ended before the media strategy."""


class Journal:
    def __init__(self, path, clock):
        self.clock, self.start = clock, clock()
        self.stream = Path(path).open("w", encoding="utf-8")

    def emit(self, event, **fields):
        item = {"schema_version": VERSION, "at_s": round(self.clock() - self.start, 6), "event": event, **fields}
        self.stream.write(json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n")
        self.stream.flush()

    def close(self):
        self.stream.close()


def source_recording(output, spans, start, end):
    """Reconstruct the scheduled local source timeline; deliberately NOT called TX capture."""
    size = max(1, min(1020 * 8000, round(max(0, end - start) * 8000)))
    samples = array.array("h", [0]) * size
    for span in spans:
        offset = max(0, round((span["start"] - start) * 8000))
        take = min(round((span["end"] - span["start"]) * 8000), size - offset)
        if take <= 0:
            continue
        with wave.open(span["file"], "rb") as reader:
            pcm = array.array("h")
            pcm.frombytes(reader.readframes(take))
            if sys.byteorder != "little":
                pcm.byteswap()
        samples[offset:offset + len(pcm)] = pcm
    if sys.byteorder != "little":
        samples.byteswap()
    with wave.open(str(Path(output) / "tx_source.wav"), "wb") as writer:
        writer.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        writer.writeframes(samples.tobytes())


def execute(plan, output, backend_factory, clock=time.monotonic):
    output = Path(output)
    journal = Journal(output / "events.jsonl", clock)
    backend, spans, active_span = None, [], None
    result = {"schema_version": VERSION, "backend": "pjsua2", "status": "failed", "scenario_sha256": plan["scenario_sha256"],
              "target_uri": plan["target_uri"], "codec_requested": plan["codec"], "steps_completed": 0,
              "business_assertions": "not_evaluated", "warnings": ["策略完成仅表示动作执行完毕，不证明 IVR 回答正确。",
              "tx_source.wav 是本地播放源按调度时间重建；不是抓取的出站 RTP，不包含带外 DTMF 声音。"]}
    def alive():
        if backend.failure:
            raise CallFailure("MEDIA_ERROR", backend.failure)
        if backend.disconnected:
            response_checks = [s for s in plan.get("assertions", []) if s["type"] == "response_code"]
            code = getattr(backend, "invite_final_code", None)
            if (not getattr(backend, "ever_connected", True) and code is not None and code >= 300
                    and getattr(backend, "last_code", None) == code
                    and response_checks and all(code in s["codes"] for s in response_checks)):
                raise ExpectedRejection()
            raise CallFailure("REMOTE_HANGUP", "对端在策略完成前结束通话")

    def finalization_error(code, exc):
        error = {"code": code, "message": str(exc)}
        if "error" in result:
            result.setdefault("secondary_errors", []).append(error)
        else:
            result["error"] = error
        if result["status"] != "interrupted":
            result["status"] = "failed"

    def until(deadline, condition=None, call_deadline=None):
        while True:
            alive()
            if condition is not None and condition():
                return
            now = clock()
            if call_deadline is not None and now >= call_deadline:
                raise CallFailure("CALL_TIMEOUT", "通话超过 max_call_s")
            if now >= deadline:
                if condition is None:
                    return
                raise CallFailure("WAIT_TIMEOUT", "等待接通、媒体或播放完成超时")
            backend.poll(max(1, min(20, int((deadline - now) * 1000))))

    journal.emit("run_start", target_uri=plan["target_uri"])
    try:
        backend = backend_factory(plan, output, journal.emit, clock)
        if getattr(backend, "observation", None) is not None:
            backend.observation.set_origin(journal.start)
        backend.dial()
        until(clock() + plan["connect_timeout_s"], lambda: backend.connected and backend.media_ready)
        answer = clock()
        deadline = answer + plan["max_call_s"]
        journal.emit("strategy_start")
        for index, step in enumerate(plan["steps"]):
            alive()
            if clock() >= deadline:
                raise CallFailure("CALL_TIMEOUT", "通话超过 max_call_s")
            action = step["action"]
            journal.emit("step_start", index=index, action=action)
            if action == "wait":
                until(clock() + step["duration_s"], call_deadline=deadline)
            elif action == "wait_audio":
                started = clock()
                until(started + step["timeout_s"],
                      lambda: backend.wait_audio_ready(step["state"], step["duration_ms"], started), deadline)
                journal.emit("audio_trigger", state=step["state"], duration_ms=step["duration_ms"], index=index)
            elif action == "dtmf":
                for digit in step["digits"]:
                    backend.dtmf(digit, step["duration_ms"], step["method"])
                    journal.emit("dtmf_sent", digit=digit, duration_ms=step["duration_ms"], method=step["method"])
                    until(clock() + (step["duration_ms"] + step["gap_ms"]) / 1000, call_deadline=deadline)
            elif action in ("play", "play_media"):
                audio = step["audio"] if action == "play" else step["media"]["audio"]
                events = [] if action == "play" else step["media"]["dtmf"]
                backend.current_step_index = index
                backend.play(audio["file"])
                started = clock()
                active_span = {"file": audio["file"], "start": started, "end": started}
                journal.emit("play_start", file=audio["file"], sha256=audio["sha256"])
                for event in events:
                    until(started + event["at_s"], call_deadline=deadline)
                    backend.dtmf(event["digit"], event["duration_ms"], "rfc4733")
                    journal.emit("dtmf_sent", digit=event["digit"], duration_ms=event["duration_ms"], method="rfc4733", media_at_s=event["at_s"])
                until(started + audio["duration_s"] + 3, backend.playback_done, deadline)
                # A file EOF can precede completion of its last telephone-event.
                if events:
                    until(started + events[-1]["at_s"] + events[-1]["duration_ms"] / 1000, call_deadline=deadline)
                active_span["end"] = min(clock(), started + audio["duration_s"])
                spans.append(active_span)
                active_span = None
                backend.stop_playback()
                journal.emit("play_complete")
            elif action == "hangup":
                backend.hangup()
            result["steps_completed"] += 1
            journal.emit("step_complete", index=index, action=action)
        result["status"] = "completed"
    except ExpectedRejection:
        result["status"] = "completed"
        result["expected_rejection"] = True
        result["steps_skipped"] = len(plan["steps"])
    except KeyboardInterrupt:
        result["status"] = "interrupted"
        result["error"] = {"code": "INTERRUPTED", "message": "操作者终止通话"}
    except Exception as exc:
        result["error"] = {"code": getattr(exc, "code", "RUNTIME_ERROR"), "message": str(exc)}
    finally:
        ended = clock()
        if active_span:
            active_span["end"] = ended
            spans.append(active_span)
        if backend:
            recording_start = backend.record_started
            try:
                result["call"] = backend.details()
            except Exception as exc:
                finalization_error("CALL_DETAILS_ERROR", exc)
            try:
                backend.close()
            except Exception as exc:
                finalization_error("CLEANUP_ERROR", exc)
            try:
                result["call"] = backend.details()
            except Exception as exc:
                finalization_error("CALL_DETAILS_ERROR", exc)
            # Close native resources before any post-processing that can fail.
            if recording_start is not None:
                try:
                    source_recording(output, spans, recording_start, ended)
                    result["recording"] = {"rx_file": "rx.wav", "tx_source_file": "tx_source.wav", "rx_role": "remote_received",
                                           "tx_role": "local_scheduled_source", "alignment": "scheduler_estimate_not_sample_exact",
                                           "starts_at_run_s": round(recording_start - journal.start, 6)}
                except Exception as exc:
                    finalization_error("RECORDING_ERROR", exc)
        result["duration_s"] = round(ended - journal.start, 6)
        try:
            journal.emit("run_end", status=result["status"])
        except Exception as exc:
            finalization_error("JOURNAL_ERROR", exc)
        finally:
            try:
                journal.close()
            except Exception as exc:
                finalization_error("JOURNAL_ERROR", exc)
        write_json(output / "result.json", result)
    return result
