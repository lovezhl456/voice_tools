"""只接受显式人工标签；与合成夹具的期望结果分开管理。"""
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re

from voice_tools.core.files import read_json, write_json
from .detector import CANDIDATES
from .reports import LABELS, REVIEW_FIELDS

DECISIONS = {"missing", "delayed", "audible", "exclude", "uncertain"}


def result_index(path):
    index = {}
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict) or record.get("schema_version") != "1.0":
                raise ValueError("不支持的结果 schema_version")
            if "error" in record:
                continue
            if (not {"sample_id", "audio_sha256", "result"} <= record.keys()
                    or not isinstance(record["result"], dict)
                    or not isinstance(record["result"].get("opportunities"), list)):
                raise ValueError("结果记录缺少样本标识或应答机会列表")
            for field in ("sample_id", "audio_sha256"):
                if not isinstance(record[field], str) or not re.fullmatch(r"[0-9a-f]{64}", record[field]):
                    raise ValueError("结果中的样本标识或摘要无效")
            for item in record["result"]["opportunities"]:
                if not isinstance(item, dict) or not {"id", "at_s", "observed_until_s", "status"} <= item.keys():
                    raise ValueError("结果中的应答机会缺少必要字段")
                if not isinstance(item["id"], str) or not item["id"] or not isinstance(item["status"], str) or item["status"] not in LABELS:
                    raise ValueError("结果中的机会标识或状态无效")
                for field in ("at_s", "observed_until_s"):
                    value = item[field]
                    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                        raise ValueError("结果中的机会时间无效")
                if item["observed_until_s"] < item["at_s"]:
                    raise ValueError("结果中的机会时间倒置")
                key = (record["sample_id"], item["id"])
                value = {"audio_sha256": record["audio_sha256"], **item}
                if key in index and index[key] != value:
                    raise ValueError("同一样本/应答机会出现相互冲突的结果")
                index[key] = value
    return index


def new_file(path):
    path = Path(path)
    if path.exists():
        raise ValueError(f"拒绝覆盖已有文件：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def timestamp(text):
    if not isinstance(text, str):
        raise ValueError("reviewed_at 必须是时间字符串")
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("reviewed_at 必须为含时区的 ISO 8601 时间") from error
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("reviewed_at 必须包含时区")
    return value.isoformat()


def check_window(label, predicted):
    if label["audio_sha256"] != predicted["audio_sha256"]:
        raise ValueError("标签的录音摘要与结果不一致")
    for key in ("at_s", "observed_until_s"):
        try:
            value = float(label[key])
        except (TypeError, ValueError) as error:
            raise ValueError(f"标签 {key} 必须是有效时间") from error
        if not math.isfinite(value) or abs(value - predicted[key]) > 1e-6:
            raise ValueError("标签窗口与结果不一致；请使用固定事件文件对齐后再评估")


def promote(review_path, results_path, output, dataset_kind):
    if dataset_kind not in ("synthetic", "real"):
        raise ValueError("dataset_kind 只能为 synthetic 或 real")
    index = result_index(results_path)
    labels, seen = [], set()
    with Path(review_path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not set(REVIEW_FIELDS) <= set(reader.fieldnames or []):
            raise ValueError("人工复核 CSV 缺少必需列，请从分析报告导出")
        for row in reader:
            # CSV 输出为电子表格防注入加的前缀，只在格式相符时还原。
            row = {k: (v[1:] if v.startswith("'") and v[1:].lstrip().startswith(("=", "+", "-", "@")) else v)
                   for k, v in row.items() if k is not None and v is not None}
            decision = row.get("decision", "").strip()
            if not decision:
                if any(row.get(k, "").strip() for k in ("reviewer", "reviewed_at", "notes")):
                    raise ValueError("存在已填写复核信息但缺少 decision 的半成品标签")
                continue
            if decision not in DECISIONS or not row.get("reviewer", "").strip():
                raise ValueError("人工标签须有合法 decision 和非空 reviewer")
            reviewed_at = timestamp(row.get("reviewed_at", ""))
            key = (row.get("sample_id"), row.get("opportunity_id"))
            if key not in index or key in seen:
                raise ValueError("标签引用了不存在或重复的样本/应答机会")
            check_window(row, index[key])
            seen.add(key)
            labels.append({"sample_id": key[0], "opportunity_id": key[1],
                           "audio_sha256": row["audio_sha256"],
                           "at_s": float(row["at_s"]), "observed_until_s": float(row["observed_until_s"]),
                           "decision": decision, "reviewer": row["reviewer"].strip(),
                           "reviewed_at": reviewed_at, "notes": row.get("notes", "")})
    if not labels:
        raise ValueError("没有人工复核标签；不能把空白或自动结果晋升为黄金集")
    golden = {"schema_version": "1.0", "label_source": "human_review", "dataset_kind": dataset_kind,
              "created_at": datetime.now(timezone.utc).isoformat(), "labels": labels}
    write_json(new_file(output), golden)
    return golden


def evaluate(golden_path, results_path):
    golden, index = read_json(golden_path), result_index(results_path)
    if not isinstance(golden, dict) or golden.get("schema_version") != "1.0" or golden.get("label_source") != "human_review":
        raise ValueError("仅接受人工复核黄金集 schema 1.0")
    if golden.get("dataset_kind") not in ("synthetic", "real"):
        raise ValueError("黄金集必须声明 synthetic / real")
    if not isinstance(golden.get("labels"), list) or not golden["labels"]:
        raise ValueError("黄金集标签为空或无效")
    counts = {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "excluded_or_uncertain": 0}
    seen = set()
    for label in golden["labels"]:
        if not isinstance(label, dict) or not {"sample_id", "opportunity_id", "audio_sha256", "at_s", "observed_until_s", "decision", "reviewer", "reviewed_at"} <= label.keys():
            raise ValueError("黄金标签缺少必要字段")
        if not all(isinstance(label[field], str) for field in ("sample_id", "opportunity_id", "decision")):
            raise ValueError("黄金标签标识或判断类型无效")
        key = (label["sample_id"], label["opportunity_id"])
        if key in seen or key not in index:
            raise ValueError("黄金标签重复或缺少对应预测；不能静默跳过")
        seen.add(key)
        decision = label["decision"]
        if decision not in DECISIONS or not isinstance(label["reviewer"], str) or not label["reviewer"].strip():
            raise ValueError("黄金标签不是有效的人工复核记录")
        timestamp(label["reviewed_at"])
        check_window(label, index[key])
        if decision in ("uncertain", "exclude"):
            counts["excluded_or_uncertain"] += 1
            continue
        positive = decision in ("missing", "delayed")
        predicted = index[key]["status"] in CANDIDATES
        counts["tp" if positive and predicted else "fn" if positive else "fp" if predicted else "tn"] += 1
    evaluated = sum(counts[k] for k in ("tp", "fp", "fn", "tn"))
    if not evaluated:
        raise ValueError("没有可计分的明确标签，无法计算指标")
    tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
    return {"schema_version": "1.0", "dataset_kind": golden["dataset_kind"], "evaluated": evaluated,
            "counts": counts, "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "notice": "仅衡量该人工标签集上的候选检出；synthetic 不能代表真实语音准确率。"}
