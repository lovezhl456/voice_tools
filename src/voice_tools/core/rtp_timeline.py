"""Bounded, payload-free RTP evidence. Small shards travel in existing task bundles."""
import json
from pathlib import Path

from .files import sha256, write_json


class TimelineWriter:
    def __init__(self, directory, shard_bytes=8 * 1024 * 1024):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.limit = shard_bytes
        self.stream = None
        self.path = None
        self.size = self.count = 0
        self.chunks = []
        self.first_epoch = self.last_epoch = None

    def close_chunk(self):
        if self.stream is None:
            return
        self.stream.close()
        self.chunks.append({"path": self.path.name, "sha256": sha256(self.path), "bytes": self.size, "rows": self.count})
        self.stream = None

    def write(self, row):
        payload = (json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n").encode()
        if len(payload) > self.limit:
            raise ValueError("单个 RTP 头记录超过时序分片额度")
        if self.stream is not None and self.size + len(payload) > self.limit:
            self.close_chunk()
        if self.stream is None:
            self.path = self.directory / f"packets-{len(self.chunks):05d}.jsonl"
            self.stream = self.path.open("xb")
            self.size = self.count = 0
        self.stream.write(payload)
        if self.first_epoch is None:
            self.first_epoch = row["epoch"]
        self.last_epoch = row["epoch"]
        self.size += len(payload)
        self.count += 1

    def abort(self):
        """Keep completed header rows inspectable when parsing is interrupted."""
        result = self.finish({"first_epoch": self.first_epoch, "last_epoch": self.last_epoch,
            "packet_limit_reached": False, "truncated_packets": 0, "out_of_order_capture_timestamps": 0})
        result.update(complete=False, interrupted=True)
        write_json(self.directory / "timeline.json", result)

    def finish(self, analysis):
        self.close_chunk()
        result = {"schema_version": "1.0", "kind": "rtp_timeline", "chunks": self.chunks,
            "complete": not any(analysis[k] for k in ("packet_limit_reached", "truncated_packets", "out_of_order_capture_timestamps")),
            "coverage_start_epoch": analysis["first_epoch"], "coverage_end_epoch": analysis["last_epoch"],
            "packet_limit_reached": analysis["packet_limit_reached"], "truncated_packets": analysis["truncated_packets"],
            "out_of_order_capture_timestamps": analysis["out_of_order_capture_timestamps"],
            "notice": "仅采集点观察到的 RTP 头；不含载荷，不证明终端播放或网络根因。"}
        write_json(self.directory / "timeline.json", result)
        return result
