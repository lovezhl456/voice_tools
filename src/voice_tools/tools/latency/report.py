"""Standalone report uses the exact same component as task review."""
import json
from pathlib import Path
import shutil

WEB = Path(__file__).parent / "web"


def copy_assets(output):
    for name in ("latency.js", "latency.css"):
        shutil.copyfile(WEB / name, Path(output) / name)


def render(output, run, rows):
    copy_assets(output)
    payload = json.dumps({"summary": run, "rows": rows, "exports": {"run.json": "run.json", "files.jsonl": "files.jsonl", "turns.csv": "turns.csv"}}, ensure_ascii=False, allow_nan=False)
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    html = (WEB / "index.html").read_text().replace("/*__DATA__*/", "window.LATENCY_DATA=" + payload + ";")
    (Path(output) / "index.html").write_text(html, encoding="utf-8")
