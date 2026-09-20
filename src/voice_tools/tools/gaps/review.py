"""Gap labels are bound to detector identity, never to a path or queue number."""
import csv
from datetime import datetime
import json
from pathlib import Path

from voice_tools.core.output_events import number

from voice_tools.core.review.gaps import GAP_LABEL_FIELDS as FIELDS

DECISIONS = {"confirmed", "normal", "excluded", "uncertain"}


def read_records(path):
    records = []
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or row.get("tool") != "gaps" or row.get("schema_version") != "1.0":
                raise ValueError("需要 gaps 1.0 结果文件")
            records.append(row)
    if not records:
        raise ValueError("结果文件为空")
    return records


def label_row(record, gap):
    return dict(zip(FIELDS[:8], ["1.0", record["audio_sha256"], record["result"]["fingerprint"], gap["id"], "automatic", gap["type"], gap["start_s"], gap["end_s"]]))


def check_rows(rows, records):
    index = {r["result"]["fingerprint"]: r for r in records if "result" in r}
    seen, labeled, manual, pending = set(), 0, 0, 0
    for row in rows:
        record = index.get(row.get("fingerprint"))
        if row.get("schema_version") != "1.0" or record is None or row.get("audio_sha256") != record["audio_sha256"]:
            raise ValueError("标注版本、录音摘要或检测规则身份不匹配")
        key = (row["fingerprint"], row.get("gap_id"))
        if not isinstance(key[1], str) or not key[1] or key in seen:
            raise ValueError("间隙标识为空或重复")
        seen.add(key)
        try:
            start, end = float(row["start_s"]), float(row["end_s"])
        except (ValueError, KeyError, TypeError):
            raise ValueError("标注时间无效") from None
        number(start, "start_s", 0, record["result"]["duration_s"])
        number(end, "end_s", start, record["result"]["duration_s"])
        if end <= start:
            raise ValueError("标注区间为空")
        if row.get("origin") == "automatic":
            original = next((g for g in record["result"]["gaps"] if g["id"] == row["gap_id"]), None)
            if original is None or original["type"] != row.get("type") or any(abs(original[k]-v)>1e-6 for k,v in (("start_s",start),("end_s",end))):
                raise ValueError("自动候选标识、类型或区间被修改")
        elif row.get("origin") == "manual" and row["gap_id"].startswith("manual-") and row.get("type") == "manual_gap":
            manual += 1
        else:
            raise ValueError("未知标注来源或手工间隙类型")
        if not row.get("decision"):
            if any(row.get(k) for k in ("reviewer", "reviewed_at", "notes")) or row["origin"] == "manual":
                raise ValueError("标注未填写完整")
            pending += 1
            continue
        if row["decision"] not in DECISIONS or not row.get("reviewer", "").strip():
            raise ValueError("标注判断或复核人无效")
        try:
            timestamp = datetime.fromisoformat(row.get("reviewed_at", "").replace("Z", "+00:00"))
            if timestamp.utcoffset() is None:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError("复核时间须包含时区") from None
        labeled += 1
    return {"labels": labeled, "manual": manual, "pending": pending, "valid": True}


def check(path, results):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != FIELDS:
            raise ValueError("间隙标注 CSV 列不匹配；不能导入 QA 应答标签")
        return check_rows(reader, read_records(results))
