"""候选筛查指标；明确区分未检出与无法判定。"""
import math
from collections import Counter, defaultdict

from .detector import CANDIDATES

ABSTAIN = {"CENSORED", "INSUFFICIENT_EVIDENCE", "EXCLUDED"}


def wilson(success, total):
    if not total:
        return None
    z = 1.959963984540054
    p, z2 = success / total, z * z
    center = (p + z2 / (2 * total)) / (1 + z2 / total)
    half = z * math.sqrt(p * (1 - p) / total + z2 / (4 * total * total)) / (1 + z2 / total)
    return [max(0, center - half), min(1, center + half)]


def score(pairs):
    counts = {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "excluded_or_uncertain": 0}
    abstained, positive_abstained = 0, 0
    for label, prediction in pairs:
        decision = label["decision"]
        if decision in ("exclude", "uncertain"):
            counts["excluded_or_uncertain"] += 1
            continue
        positive = decision in ("missing", "delayed")
        if prediction["status"] in ABSTAIN:
            abstained += 1
            positive_abstained += int(positive)
            continue
        predicted = prediction["status"] in CANDIDATES
        counts["tp" if positive and predicted else "fn" if positive else "fp" if predicted else "tn"] += 1
    tp, fp, fn, tn = (counts[x] for x in ("tp", "fp", "fn", "tn"))
    evaluated = tp + fp + fn + tn
    conservative_denominator = tp + fn + positive_abstained
    return {"counts": counts, "evaluated": evaluated, "abstained": abstained,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "precision_denominator": tp + fp, "recall_denominator": tp + fn,
            "precision_ci95": wilson(tp, tp + fp), "recall_ci95": wilson(tp, tp + fn),
            "recall_including_abstentions": tp / conservative_denominator if conservative_denominator else None,
            "false_alarms_per_100_scored": fp * 100 / evaluated if evaluated else None,
            "scoring_coverage": evaluated / (evaluated + abstained) if evaluated + abstained else None}


def detailed_metrics(labels, index):
    pairs = [(label, index[(label["sample_id"], label["opportunity_id"])]) for label in labels]
    metrics = score(pairs)
    groups = defaultdict(list)
    for label, prediction in pairs:
        for field in ("scenario", "line_id", "split"):
            values = str(label.get(field) or "未标注").split(";") if field == "scenario" else [label.get(field) or "未标注"]
            for value in set(str(x).strip() for x in values if str(x).strip()):
                groups[(field, value)].append((label, prediction))
    by_class = {}
    for decision, status in (("missing", "NO_OUTPUT_CANDIDATE"), ("delayed", "LATE_OUTPUT_CANDIDATE")):
        tp = fp = fn = 0
        for label, prediction in pairs:
            if label["decision"] in ("exclude", "uncertain"):
                continue
            positive = label["decision"] == decision
            predicted = prediction["status"] == status
            tp += int(positive and predicted); fp += int(not positive and predicted); fn += int(positive and not predicted)
        by_class[decision] = {"tp": tp, "fp": fp, "fn": fn, "precision": tp / (tp + fp) if tp + fp else None,
                              "recall": tp / (tp + fn) if tp + fn else None,
                              "precision_ci95": wilson(tp, tp + fp), "recall_ci95": wilson(tp, tp + fn),
                              "notice": "该类召回分母包含无法判定的正例"}
    return {**metrics, "by_class": by_class,
            "strata": [{"field": field, "value": value, "labels": len(items), **score(items)} for (field, value), items in sorted(groups.items())],
            "label_coverage": len(labels) / len(index) if index else None,
            "predicted_opportunities": len(index), "reviewed_labels": len(labels),
            "evidence_counts": dict(Counter(p.get("evidence_level", "unknown") for p in index.values())),
            "prediction_status_counts": dict(Counter(p["status"] for p in index.values()))}
