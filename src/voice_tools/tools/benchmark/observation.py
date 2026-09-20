"""Bounded callback inbox; PCM/VAD/disk work is performed by the SIP worker loop."""
import json
import queue
import wave

import numpy as np

from voice_tools.core.files import write_json

RATE = 8000


class Activity:
    """20ms activity candidates, never a transcription or an intent decision."""
    def __init__(self, config):
        self.floor = 10 ** (config["threshold_db"] / 20)
        self.vad = None
        if config["backend"] == "webrtcvad":
            try:
                import webrtcvad
            except ImportError as exc:
                raise ValueError("时序评测需要可选依赖：pip install -e '.[vad]'") from exc
            self.vad = webrtcvad.Vad(2)

    def voiced(self, pcm):
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64) / 32768
        if not len(samples):
            return False
        samples -= samples.mean()
        energy = float(np.sqrt(np.mean(samples * samples))) >= self.floor
        if self.vad is None:
            return energy
        return len(pcm) == 320 and energy and self.vad.is_speech(pcm, RATE)


class Observation:
    def __init__(self, output, clock, detector, queue_size=2048):
        self.output, self.clock, self.origin = output, clock, clock()
        self.inbox = queue.Queue(maxsize=queue_size)
        self.detector = detector
        self.activity = Activity(detector)
        self.positions = {"rx": 0, "tx": 0}
        self.sequences = {"rx": 0, "tx": 0}
        self.saved_samples = {"rx": 0, "tx": 0}
        self.dropped = 0
        self.issues = []
        self.closed = False
        self.epoch = 0
        self.last = {}
        self.rx_state = None
        self.rx_since = self.rx_until = 0.0
        self.writers = {}
        for direction in ("rx", "tx"):
            writer = wave.open(str(output / f"bridge_{direction}.wav"), "wb")
            writer.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
            self.writers[direction] = writer
        self.events = (output / "media-frames.jsonl").open("w", encoding="utf-8")
        # An unfinished manifest is evidence even if the native process crashes.
        self.save(False)

    def set_origin(self, origin):
        self.origin = origin

    def issue(self, reason):
        if reason not in self.issues:
            self.issues.append(reason)

    def submit(self, direction, pcm, at=None, source=None):
        """Called on the media thread. No VAD, file writes, or blocking queue puts."""
        if self.closed:
            return
        at = self.clock() if at is None else at
        count = len(pcm) // 2
        item = (direction, bytes(pcm), at, self.positions[direction], self.sequences[direction], self.epoch, source)
        self.positions[direction] += count
        self.sequences[direction] += 1
        try:
            self.inbox.put_nowait(item)
        except queue.Full:
            self.dropped += 1

    def media_changed(self):
        self.epoch += 1
        if self.epoch > 1:
            self.issue("media_recreated")

    def drain(self):
        while True:
            try:
                direction, pcm, at, source_sample, sequence, epoch, source = self.inbox.get_nowait()
            except queue.Empty:
                return
            if len(pcm) % 2 or not pcm:
                self.issue("invalid_pcm_frame")
                continue
            relative = at - self.origin
            samples = len(pcm) // 2
            previous = self.last.get(direction)
            if previous is not None:
                delta = relative - previous[0]
                same_play = direction == "rx" or (source or {}).get("step_index") == previous[2]
                if delta < 0 or (same_play and delta > max(.08, 3 * previous[1] / RATE)):
                    self.issue("media_clock_discontinuity")
            self.last[direction] = (relative, samples, (source or {}).get("step_index"))
            offset = self.saved_samples[direction]
            self.writers[direction].writeframesraw(pcm)
            self.saved_samples[direction] += samples
            record = {"direction": direction, "at_s": round(relative, 6), "sample_start": offset,
                      "source_sample": source_sample, "samples": samples, "sequence": sequence, "epoch": epoch}
            if source is not None:
                record["playback"] = source
            self.events.write(json.dumps(record, allow_nan=False) + "\n")
            if direction == "rx":
                for off in range(0, len(pcm), 320):
                    chunk = pcm[off:off + 320]
                    state = "active" if self.activity.voiced(chunk) else "silent"
                    start = relative + off / 16000
                    if state != self.rx_state:
                        self.rx_state, self.rx_since = state, start
                    self.rx_until = start + len(chunk) / 16000

    def matches(self, state, duration_ms, since):
        self.drain()
        fresh = self.clock() - self.origin - self.rx_until < .1
        start = max(self.rx_since, since - self.origin)
        return fresh and self.rx_state == state and self.rx_until - start >= duration_ms / 1000

    def info(self, complete=None):
        if complete is None:
            complete = self.closed
        return {"schema_version": "1.0", "sample_rate": RATE, "channels": 1, "sample_width": 2,
                "timebase": "run_monotonic", "observation_point": "local_pjsua_media_bridge",
                "rx_role": "received_pcm_after_jitter_buffer", "tx_role": "local_pcm_submitted_to_bridge_not_rtp",
                "audio": {"rx": "bridge_rx.wav", "tx": "bridge_tx.wav"}, "frames": "media-frames.jsonl",
                "samples": dict(self.saved_samples), "dropped_frames": self.dropped,
                "issues": list(self.issues), "complete": complete, "detector": self.detector,
                "resolution_ms": 20, "not_measured": ["wire_rtp_timing", "speaker_output", "semantic_turns"]}

    def save(self, complete):
        write_json(self.output / "media-observation.json", self.info(complete))

    def close(self):
        if self.closed:
            return
        self.closed = True
        complete = False
        try:
            self.drain()
            complete = True
        except Exception:
            self.issue("observation_write_failed")
            raise
        finally:
            # Attempt every close even after a write failure; never label partial PCM complete.
            try:
                for writer in self.writers.values():
                    try:
                        writer.close()
                    except Exception:
                        complete = False
                        self.issue("observation_close_failed")
                try:
                    self.events.close()
                except Exception:
                    complete = False
                    self.issue("observation_close_failed")
            finally:
                self.save(complete)
