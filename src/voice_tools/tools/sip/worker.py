"""Native process boundary: logs never corrupt the parent's JSON stdout."""
import signal
import sys
from pathlib import Path

from voice_tools.core.files import read_json, write_json


def main():
    plan_file, output = Path(sys.argv[1]), Path(sys.argv[2])
    stopping = False
    def interrupted(signum, frame):
        nonlocal stopping
        if stopping:
            return
        stopping = True
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        from .pjsua import Backend
        from .runner import execute
        result = execute(read_json(plan_file), output, Backend)
        return 0 if result["status"] == "completed" else 3
    except Exception as exc:
        write_json(output / "result.json", {"schema_version": "1.0", "status": "failed", "backend": "pjsua2",
                                           "error": {"code": "WORKER_ERROR", "message": str(exc)}})
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
