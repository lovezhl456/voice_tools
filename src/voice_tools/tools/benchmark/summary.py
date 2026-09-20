"""Keep invalid, failed and unobserved calls in the denominator."""
import csv
from pathlib import Path

import numpy as np

from voice_tools.core.files import read_json, write_json
from .analysis import analyze


def distribution(reports):
    values = {}
    counts = {"valid": 0, "invalid": 0, "insufficient_evidence": 0, "failed": 0}
    for report in reports:
        if report.get("execution_status") not in ("completed", "findings"):
            counts["failed"] += 1
        elif report.get("status") == "findings":
            counts["failed"] += 1
        elif any(m["status"] == "invalid" for m in report.get("metrics", [])):
            counts["invalid"] += 1
        elif report.get("status") in ("insufficient_evidence", "partial") or not report.get("metrics"):
            counts["insufficient_evidence"] += 1
        else:
            counts["valid"] += 1
        for metric in report.get("metrics", []):
            if metric["status"] == "measured" and metric["value_ms"] is not None:
                values.setdefault(metric["kind"], []).append(metric["value_ms"])
    statistics = {}
    for kind, samples in values.items():
        statistics[kind] = {"n": len(samples), "p50_ms": round(float(np.percentile(samples, 50)), 2),
                            "p95_ms": round(float(np.percentile(samples, 95)), 2),
                            "p99_ms": round(float(np.percentile(samples, 99)), 2),
                            "min_ms": min(samples), "max_ms": max(samples),
                            "note": "仅有效测量值的经验分位数；小样本尾部分位数不稳定"}
    return {"calls": len(reports), "counts": counts, "metrics": statistics}


def batch_jobs(path):
    """Validate receipt structure while retaining unfinished jobs as observations."""
    batch = read_json(path)
    if not isinstance(batch, dict) or batch.get("schema_version") != "1.0" or batch.get("kind") != "sip_batch_result":
        raise ValueError("批次回执须为 schema_version=1.0、kind=sip_batch_result")
    if batch.get("status") not in ("planned", "running", "completed", "failed", "interrupted"):
        raise ValueError("批次回执 status 缺失或无效")
    jobs = batch.get("jobs")
    if not isinstance(jobs, list) or not 1 <= len(jobs) <= 10000:
        raise ValueError("批次回执 jobs 须为包含 1–10000 项的数组")
    ids = set()
    states = ("pending", "running", "planned", "completed", "failed", "cancelled", "interrupted")
    for index, job in enumerate(jobs):
        if not isinstance(job, dict):
            raise ValueError(f"批次回执 jobs[{index}] 须为对象")
        identifier = job.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in ids:
            raise ValueError(f"批次回执 jobs[{index}].id 缺失或重复")
        ids.add(identifier)
        if job.get("status") not in states:
            raise ValueError(f"批次回执 jobs[{index}].status 缺失或无效")
        if "name" in job and not isinstance(job["name"], str):
            raise ValueError(f"批次回执 jobs[{index}].name 须为文本")
        if "output" in job or job["status"] == "completed":
            if not isinstance(job.get("output"), str) or not job["output"]:
                raise ValueError(f"批次回执 jobs[{index}].output 缺失或无效")
    return jobs


def summarize(batch_dir, output):
    root = Path(batch_dir).resolve()
    jobs = batch_jobs(root / "batch-result.json")
    reports = []
    for job in jobs:
        report = {"case_id": job.get("name", job.get("id")), "tags": [], "metrics": [],
                  "status": "insufficient_evidence", "execution_status": job.get("status"), "issues": []}
        try:
            directory = (root / job["output"]).resolve()
            if root not in directory.parents:
                raise ValueError("批次结果路径越界")
            report = analyze(directory)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            report["issues"].append(str(exc))
        reports.append(report)
    groups = {}
    for report in reports:
        for tag in set(report.get("tags", [])):
            groups.setdefault(tag, []).append(report)
    result = {"schema_version": "1.0", "kind": "benchmark_summary", **distribution(reports),
              "groups": {tag: distribution(items) for tag, items in sorted(groups.items())}, "reports": reports}
    write_json(Path(output) / "benchmark-summary.json", result)
    with (Path(output) / "benchmark-summary.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["case", "execution_status", "metric", "status", "value_ms", "verdict"])
        for report in reports:
            for metric in report.get("metrics", []) or [{}]:
                values = [report["case_id"], report.get("execution_status"), metric.get("kind"), metric.get("status", report["status"]), metric.get("value_ms"), metric.get("verdict")]
                writer.writerow(["'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v for v in values])
    return result
