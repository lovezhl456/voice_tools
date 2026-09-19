"""供命令适配层使用的稳定 JSON 封套。"""
import json
from pathlib import Path

from voice_tools import __version__


def emit_result(args, summary, message, artifacts=None, exit_code=0):
    if getattr(args, "json_output", False):
        result = {"schema_version": "1.0", "tool_version": __version__, "tool": args.tool,
                  "action": args.action, "ok": exit_code in (0, 1), "exit_code": exit_code,
                  "status": "completed" if exit_code == 0 else "findings" if exit_code == 1 else "partial_error",
                  "summary": summary, "artifacts": {k: str(Path(v).resolve()) for k, v in (artifacts or {}).items()}}
        if exit_code == 3:
            result["error"] = {"code": "PARTIAL_FAILURE", "message": "部分输入或操作未完整完成；请检查产物中的错误、状态和分析限制"}
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    else:
        print(message)
    return exit_code


def emit_error(tool, action, message, code="INVALID_INPUT"):
    print(json.dumps({"schema_version": "1.0", "tool_version": __version__, "tool": tool, "action": action,
                      "ok": False, "status": "error", "exit_code": 2,
                      "error": {"code": code, "message": str(message)}, "artifacts": {}}, ensure_ascii=False))


def artifacts(output, *names):
    return {name: Path(output) / name for name in names}
