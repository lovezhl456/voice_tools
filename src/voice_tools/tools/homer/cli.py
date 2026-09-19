"""将原 HOMER CLI 接到工具导航，业务与参数协议由 client 管理。"""


def register(commands):
    parser = commands.add_parser("homer", help="HOMER 7 SIP 查询、追踪、导出与风险分析")
    parser.set_defaults(run_argv=main)


def main(argv=None):
    from .client import main as run
    return run(argv, prog="voice-tools homer")
