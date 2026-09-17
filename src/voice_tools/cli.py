"""顶层只装配工具，不承载业务逻辑。"""
import argparse
import importlib

from . import __version__
from .tools import BUILTIN_TOOLS


def main(argv=None):
    parser = argparse.ArgumentParser(prog="voice-tools", description="本地语音工具集（CPU）")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="tool", required=True)
    for module in BUILTIN_TOOLS:
        importlib.import_module(module).register(commands)
    args = parser.parse_args(argv)
    try:
        return args.run(args) or 0
    except (ValueError, OSError) as error:
        parser.exit(2, f"错误：{error}\n")
