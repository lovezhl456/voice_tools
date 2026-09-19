"""每个工具提供 register(subparsers)，新增工具仅在此注册。"""

BUILTIN_TOOLS = (
    "voice_tools.tools.audio.cli",
    "voice_tools.tools.recording_qa.cli",
    "voice_tools.tools.homer.cli",
    "voice_tools.tools.capture.cli",
    "voice_tools.tools.report.cli",
    "voice_tools.tools.sessions.cli",
)
