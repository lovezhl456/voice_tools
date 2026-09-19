#!/usr/bin/env python3
"""Example AI tool boundary. Connection settings are owned by the operator."""
import json
import subprocess
import sys

CLI = [sys.executable, "-m", "voice_tools", "homer"]
ALLOWED = {"fields", "search", "trace", "message", "analyze", "schema"}
OPERATOR_ONLY = {"--url", "--config", "--username", "--ca-file", "--input"}


def main(args):
    if not args or args[0] not in ALLOWED or any(a.split("=", 1)[0] in OPERATOR_ONLY for a in args[1:]):
        print(json.dumps({"exit_code": 2, "error": "Unsupported AI command or operator-owned option."}))
        return 2
    try:
        result = subprocess.run([*CLI, *args], shell=False,
                                capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        print(json.dumps({"exit_code": 4, "error": "AI tool exceeded its 300 second deadline; query outcome is incomplete."}))
        return 4
    stream = result.stdout if result.returncode in (0, 6) else result.stderr
    try:
        output = json.loads(stream)
    except ValueError:
        output = {"error": "CLI did not return JSON; help/version commands are not tool results."}
    print(json.dumps({"exit_code": result.returncode, "result": output}, ensure_ascii=False, indent=2))
    return result.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
