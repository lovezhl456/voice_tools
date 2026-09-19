"""自包含复核页面；仅显式 include_audio 时输出试听副本。"""
import json
from pathlib import Path

from voice_tools.audio.health import waveform
from voice_tools.audio.io import write_wav
from voice_tools.core.files import write_json


def add_previews(output, record, audio, include_audio):
    record["waveform"] = waveform(audio)
    if not include_audio:
        return
    folder = output / "audio"
    folder.mkdir(exist_ok=True)
    sources = {"both": record["audio_copy"]}
    for channel in range(audio.samples.shape[1]):
        path = folder / f"{record['audio_sha256']}-ch{channel}.wav"
        if not path.exists():
            write_wav(path, audio.samples[:, channel], audio.sample_rate)
        sources["left" if channel == 0 else "right"] = str(path.relative_to(output))
    record["playback_sources"] = sources
    clips = {}
    for index, op in enumerate(record["result"]["opportunities"]):
        start = max(0, op["at_s"] - 2)
        end = min(audio.duration_s, op["observed_until_s"] + 1)
        a, b = round(start * audio.sample_rate), round(end * audio.sample_rate)
        path = folder / f"{record['sample_id']}-{index}.wav"
        write_wav(path, audio.samples[a:b], audio.sample_rate)
        metadata = {"schema_version": "1.0", "sample_id": record["sample_id"], "audio_sha256": record["audio_sha256"],
                    "opportunity_id": op["id"], "original_start_s": a / audio.sample_rate,
                    "original_end_s": b / audio.sample_rate, "sample_rate": audio.sample_rate,
                    "channel_map": list(range(audio.samples.shape[1]))}
        write_json(path.with_suffix(".json"), metadata)
        clips[op["id"]] = {"audio": str(path.relative_to(output)), "metadata": str(path.with_suffix(".json").relative_to(output)), **metadata}
    record["clips"] = clips


def render_workbench(output, records, summary, hide_paths=False):
    from .reports import LABELS, EVIDENCE, REVIEW_FIELDS, EXTRA_REVIEW_FIELDS
    fields = ("sample_id", "audio_sha256", "result", "waveform", "playback_sources", "clips", "error")
    data = {"records": [{**{k: r[k] for k in fields if k in r}, "input": Path(r["input"]).name if hide_paths else r["input"]} for r in records],
            "summary": summary, "labels": LABELS, "evidence": EVIDENCE,
            "fields": REVIEW_FIELDS + EXTRA_REVIEW_FIELDS}
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = Path(__file__).with_name("review.html").read_text(encoding="utf-8")
    script = Path(__file__).with_name("review.js").read_text(encoding="utf-8")
    (output / "review.html").write_text(template.replace("__REVIEW_SCRIPT__", script).replace("__REVIEW_DATA__", payload), encoding="utf-8")
