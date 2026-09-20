"""Gap-specific data and labels on the shared waveform/playback shell."""
import csv
import html
import json
from pathlib import Path
import shutil
import wave

import numpy as np

from voice_tools.audio.health import waveform
from .review import FIELDS, label_row


def previews(output, record, audio, include_audio):
    record["waveform"] = waveform(audio)
    if not include_audio:
        return
    folder = output / "audio"
    folder.mkdir(exist_ok=True)
    target = folder / (record["audio_sha256"] + ".wav")
    if not target.exists():
        shutil.copyfile(record["input"], target)
    record["playback_sources"] = {"both": str(target.relative_to(output))}
    for channel in range(audio.samples.shape[1]):
        part = folder / f"{record['audio_sha256']}-ch{channel}.wav"
        if not part.exists():
            with wave.open(str(part), "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(audio.sample_rate)
                for offset in range(0, len(audio.samples), 65536):
                    chunk = audio.samples[offset:offset + 65536, channel]
                    stream.writeframes((np.clip(chunk, -1, 32767 / 32768) * 32768).astype("<i2").tobytes())
        record["playback_sources"]["left" if channel == 0 else "right"] = str(part.relative_to(output))


def write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            safe = {}
            for key in fields:
                value = str(row.get(key, ""))
                safe[key] = "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value
            writer.writerow(safe)



def render(output, records, summary, hide_paths=False):
    with (output / "results.jsonl").open("w", encoding="utf-8") as stream:
        for row in records:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    write_csv(output / "review.csv", FIELDS, (label_row(r,g) for r in records if "result" in r for g in r["result"]["gaps"]))
    write_csv(output / "summary.csv", ["audio_sha256", "status", "candidates", "clusters", "error"],
        ({"audio_sha256": r.get("audio_sha256", ""), "status": r.get("result", {}).get("status", "ERROR"),
          "candidates": r.get("result", {}).get("candidate_count", 0), "clusters": r.get("result", {}).get("cluster_count", 0),
          "error": r.get("error", r.get("evidence", {}).get("error", ""))} for r in records))
    sections = []
    for r in records:
        name = Path(r["input"]).name if hide_paths else r["input"]
        details = {k:r[k] for k in ("result", "error", "evidence") if k in r}
        sections.append(f"<section><h2>{html.escape(name)}</h2><pre>{html.escape(json.dumps(details, ensure_ascii=False, indent=2))}</pre></section>")
    (output / "report.html").write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>输出间隙报告</title><style>body{font:16px system-ui;max-width:1100px;margin:32px auto;padding:16px}pre{white-space:pre-wrap;overflow-wrap:anywhere}section{border-top:1px solid #ccc}</style><h1>输出间隙报告</h1><p>候选与旁证不代表故障根因或通话通过。</p><a href="review.html">打开时间轴试听与人工标注</a>' + "".join(sections) + '</html>', encoding="utf-8")
    from voice_tools.core.review.gaps import render_gap_review
    render_gap_review(output / "review.html", records, summary, hide_paths)
