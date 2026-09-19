"""顶层只装配工具，不承载业务逻辑。"""
import argparse
import importlib
import json
import sys

from . import __version__
from .tools import BUILTIN_TOOLS


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    machine = bool(argv and argv[0] == "--json")
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            if machine:
                raise ValueError(message)
            super().error(message)
    parser = Parser(prog="voice-tools", description="语音工具集：录音质检与通话排障")
    parser.add_argument("--json", dest="json_output", action="store_true", help="机器可读 JSON；放在工具名称前")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="tool", required=True)
    for module in BUILTIN_TOOLS:
        importlib.import_module(module).register(commands)
    schema = commands.add_parser("schema", help="离线输出命令参数与机器接入契约")
    schema.add_argument("--tool", dest="selected_tool", choices=tuple(name for name in commands.choices if name != "schema"))
    def run_schema(args):
        from .core.schema import contract
        from .tools.homer.client import build_parser
        print(json.dumps(contract(parser, args.selected_tool, {"homer": build_parser()}), ensure_ascii=False, allow_nan=False))
        return 0
    schema.set_defaults(run=run_schema)
    # 已有独立 CLI 的工具可自行解析参数，保留它的帮助、错误格式和退出码。
    routing = argv[1:] if machine else argv
    if routing and routing[0] in commands.choices:
        run_argv = commands.choices[routing[0]].get_default("run_argv")
        if run_argv is not None:
            return run_argv(routing[1:]) or 0
    args = None
    try:
        args = parser.parse_args(argv)
        return args.run(args) or 0
    except (ValueError, OSError) as error:
        if machine:
            from .core.command import emit_error
            emit_error(getattr(args, "tool", None), getattr(args, "action", None), error)
            return 2
        parser.exit(2, f"错误：{error}\n")
