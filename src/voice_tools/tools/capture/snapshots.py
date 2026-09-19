"""周期取样，保存白名单字段；不记录完整 channel dump。"""
import json
import re
import shlex
from urllib.parse import unquote
from uuid import UUID

from .service import flow, utc_now


def snapshot(ssh, fs_cli="fs_cli", max_channels=100, offset=0):
    observed = utc_now()
    raw = ssh.checked([fs_cli, "-x", "show channels as json"], timeout=10)
    try:
        data = json.loads(raw)
        rows = data["rows"]
        if not isinstance(rows, list):
            raise ValueError()
        ids = sorted({str(UUID(row["uuid"])) for row in rows})
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError("show channels as json 未返回有效 UUID 列表") from error
    # Rotate through overloaded switches instead of permanently excluding the same tail.
    offset = offset % max(1, len(ids))
    selected = (ids[offset:] + ids[:offset])[:max_channels]
    warnings = []
    if len(ids) > len(selected):
        warnings.append(f"活动腿 {len(ids)} 条，本轮只读取 {len(selected)} 条；后续轮转，短通话可能遗漏。")
    if not selected:
        return [], warnings
    commands = []
    for uuid in selected:
        commands.append("printf '\\n__VOICE_UUID__:%s\\n' " + shlex.quote(uuid))
        commands.append(shlex.join(["timeout", "--kill-after=1s", "2s", fs_cli, "-x", f"uuid_dump {uuid}"]))
    # One SSH round trip for many dumps; 8-second remote budget also bounds disconnects.
    result = ssh.run(["timeout", "--kill-after=1s", "8s", "sh", "-c", "; ".join(commands)], timeout=15)
    records = []
    for block in result.stdout.split("\n__VOICE_UUID__:")[1:]:
        uuid, _, body = block.partition("\n")
        if uuid not in selected:
            continue
        values = {}
        for line in body.splitlines():
            key, sep, value = line.partition(":")
            if sep:
                values[key.strip().removeprefix("variable_")] = unquote(value.strip())
        call_id = values.get("sip_call_id", "")
        if not call_id or len(call_id) > 1024 or any(ord(c) < 32 for c in call_id):
            continue
        item = {"schema_version": "1.0", "observed_at": observed, "uuid": uuid, "call_id": call_id,
                "caller": values.get("Caller-Caller-ID-Number", "")[:256],
                "callee": values.get("Caller-Destination-Number", "")[:256]}
        peer = values.get("bridge_uuid") or values.get("signal_bond")
        try:
            item["peer_uuid"] = str(UUID(peer)) if peer else None
        except ValueError:
            item["peer_uuid"] = None
        try:
            item["flow"] = flow(values.get("local_media_ip", ""), values.get("local_media_port", ""),
                                values.get("remote_media_ip", ""), values.get("remote_media_port", ""))
        except ValueError:
            item["flow"] = None
        records.append(item)
    if result.returncode or len(records) < len(selected):
        warnings.append("本轮部分 UUID 已结束、缺少 Call-ID 或查询超时；会话快照不保证覆盖所有通话。")
    return records, warnings
