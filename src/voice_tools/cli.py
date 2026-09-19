"""顶层只装配工具，不承载业务逻辑。"""
import argparse
import importlib
import sys

from . import __version__
from .tools import BUILTIN_TOOLS


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="voice-tools", description="语音工具集：录音质检与通话排障")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="tool", required=True)
    for module in BUILTIN_TOOLS:
        importlib.import_module(module).register(commands)
    # 已有独立 CLI 的工具可自行解析参数，保留它的帮助、错误格式和退出码。
    if argv and argv[0] in commands.choices:
        run_argv = commands.choices[argv[0]].get_default("run_argv")
        if run_argv is not None:
            return run_argv(argv[1:]) or 0
    args = parser.parse_args(argv)
    try:
        return args.run(args) or 0
    except (ValueError, OSError) as error:
        parser.exit(2, f"错误：{error}\n")
