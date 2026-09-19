#!/usr/bin/env python3
"""Render the committed SIP case summary without native libraries or local evidence."""
import argparse
import html
import json
from pathlib import Path


def h(value):
    return html.escape(str(value), quote=True)


def render(data):
    cards = []
    for case in data["cases"]:
        steps = "".join("<li><code>" + h(json.dumps(step, ensure_ascii=False)) + "</code></li>"
                        for step in case["steps"])
        assertions = "".join(
            "<tr><td>" + h(item["name"]) + "</td><td>" + ("通过" if item["passed"] else "未通过") + "</td></tr>"
            for item in case["assertions"])
        sources = " · ".join('<a href="' + h(url) + '">' + h(url.rsplit("/", 1)[-1]) + "</a>"
                             for url in case["source_urls"])
        audio = case.get("metrics", {}).get("rx", {})
        metrics = "无音频成功断言；检查预期失败分类。"
        if audio:
            metrics = f"接收 {audio['duration_s']:.2f} 秒，RMS {audio['rms']:.0f}"
            if "correlation" in audio:
                metrics += f"，分段相关系数中位数 {audio['correlation']:.3f}"
        cards.append(f'''<details class="case" data-phase="{h(case['phase'])}">
<summary><span>{h(case['id'])} · {h(case['title'])}</span><b>{h(case['status'])}</b></summary>
<div class="body"><p><strong>前提：</strong>{h(case['precondition'])}</p>
<p><strong>预期：</strong>{h(case['expected'])} 编码 {h(case['codec'])}。</p>
<div class="columns"><section><h3>步骤</h3><ol>{steps}</ol></section>
<section><h3>实测断言</h3><table><thead><tr><th>检查项</th><th>结果</th></tr></thead><tbody>{assertions}</tbody></table></section></div>
<p>{h(metrics)}。CLI 退出码 {h(case['exit_code'])}；最后 SIP 状态 {h(case['last_sip_code'])}。</p>
<p>执行时间：{h(case['started_at'])}；原始验收 JSON 的 SHA256：<code>{h(case['acceptance_sha256'])}</code>。</p>
<p>GitHub／服务官方依据：{sources}</p></div></details>''')
    return TEMPLATE.replace("__DATE__", h(data["execution_date"])).replace("__CARDS__", "\n".join(cards))


TEMPLATE = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,"><title>SIP 自动拨测 · 20 项用例说明</title>
<style>
:root{color-scheme:light;font-family:system-ui,-apple-system,sans-serif;color:#203b34;background:#f4f6f2;line-height:1.65}
*{box-sizing:border-box}body{margin:0}main{max-width:1120px;margin:auto;padding:32px 24px 64px}h1{font-size:clamp(28px,5vw,46px);line-height:1.2}
a{color:#00684a}code{font:12px ui-monospace,monospace;overflow-wrap:anywhere}.intro,.note{padding:20px;background:#e4eee7;border-radius:12px}
.stats{display:flex;gap:16px;flex-wrap:wrap;margin:24px 0}.stats span{background:white;padding:12px 20px;border-radius:8px}
.filters{display:flex;gap:12px;flex-wrap:wrap;margin:20px 0}input,select,button{font:inherit;padding:8px 12px;border:1px solid #b9cfc0;border-radius:7px;background:white;max-width:100%}
label{display:flex;gap:8px;align-items:center}.case{background:white;margin:14px 0;border:1px solid #d7e3da;border-radius:10px;overflow:hidden}
summary{cursor:pointer;padding:18px;display:flex;justify-content:space-between;gap:15px}summary b{color:#00684a}.body{padding:0 22px 20px;font-size:14px}
.columns{display:grid;grid-template-columns:1fr 1fr;gap:22px}ol{padding-left:20px}li{margin-bottom:8px}table{width:100%;border-collapse:collapse}td,th{text-align:left;border-bottom:1px solid #d7e3da;padding:8px}p{overflow-wrap:anywhere}
@media(max-width:700px){main{padding:18px 14px}.columns{grid-template-columns:1fr}summary{padding:14px}.body{padding:0 14px 14px}.filters label:first-child{flex-basis:100%}input{min-width:0;width:100%}.stats{gap:8px}.stats span{padding:8px}}
</style><main><nav><a href="../../README.md">voice_tools</a> · <a href="../sip.md">人工手册</a> · <a href="../sip-ai.md">大模型协议</a></nav>
<h1>SIP 自动拨测<br>用例说明与结果摘要</h1>
<p class="intro">执行日期 __DATE__。按 pjsua 快速检查 → Baresip 独立互通 → 公共服务检查公网路径，验证真实 SIP UDP、G.711、按键与录音。</p>
<div class="stats"><span>pjsua 9 / 9 通过</span><span>Baresip 7 / 7 通过</span><span>公网 4 / 4 通过</span><span>Baresip 官方自测 6 / 6 通过</span></div>
<p>本页是已执行验收的便携摘要，不是重新执行测试。<a href="results.json">结果与来源摘要 JSON</a> 保留断言、时间、媒体指标和原始文件摘要；完整录音、PCAP、日志与失败尝试保留在原本地证据包，不随仓库发布。本页可直接用浏览器打开。</p>
<h2>来源与判定</h2><p>PJSIP 2.17 的 mod_call、mod_media_playrec 与 WAV/早挂断场景；Baresip 固定提交 f48c14bc 的 call、play、ausrc 测试。每个用例下方列出精确来源。测试经过适配和扩展，没有宣称完整运行两个上游套件。</p>
<p>音频匹配使用十段 100 ms 信号：至少八段相关系数 ≥ 0.65，中位数 ≥ 0.85，时间偏移变化 ≤ 120 ms。公告／音乐只检查有效接收音频，不判断语义。DTMF 核对对端日志或回送的完整字符序列，保留重复数字。</p>
<h2>20 项用例</h2><div class="filters"><label>搜索<input id="search" type="search" placeholder="DTMF、PCAP、P01…"></label>
<label>阶段<select id="phase"><option value="">全部</option><option value="pjsua">pjsua</option><option value="baresip">Baresip</option><option value="public">公共服务</option></select></label>
<button id="expand" type="button">展开当前用例</button><button id="collapse" type="button">收起全部</button></div><p id="count" aria-live="polite"></p>
__CARDS__
<h2>复现</h2><p>按 <a href="../sip-interop.md">互通验收文档</a> 配置 PJSUA2、pjsua 和 Baresip。执行器为 <code>python -m tests.sip.interop --phase pjsua|baresip|public --out NEW_DIRECTORY</code>，可加 <code>--case P01</code>。每次使用新目录；public 明确触发实际公网呼叫，普通单元测试不会拨公网。</p>
<p>在仓库根目录执行 <code>python scripts/sip_case_catalog.py</code> 可从已提交的 results.json 重建本页，无需原生 SIP 库或本地证据目录。完整录音报告使用 <code>scripts/sip_interop_report.py</code>。</p>
<div class="note"><strong>结果边界</strong><p>只使用合成音频和公开测试分机；这是有日期的短通话验证，不是长期可用性、真实网关或 IVR 业务正确性承诺。rx.wav 是实际接收音频；tx_source.wav 是本地素材时间轴重建，不证明远端收到。PCAP 用例走“音频＋去重按键”路径，SIPp 原包直放尚未完成权限条件下的验收。</p></div>
</main><script>
const cases=[...document.querySelectorAll('.case')],search=document.querySelector('#search'),phase=document.querySelector('#phase');
function filter(){let n=0;for(const c of cases){c.hidden=!!((phase.value&&c.dataset.phase!==phase.value)||(search.value&&!c.textContent.toLowerCase().includes(search.value.toLowerCase())));if(!c.hidden)n++;}document.querySelector('#count').textContent=`显示 ${n} / ${cases.length} 个用例`;}
search.addEventListener('input',filter);phase.addEventListener('change',filter);document.querySelector('#expand').onclick=()=>cases.filter(c=>!c.hidden).forEach(c=>c.open=true);document.querySelector('#collapse').onclick=()=>cases.forEach(c=>c.open=false);filter();
</script></html>'''


def main():
    default = Path(__file__).resolve().parents[1] / "docs/sip-cases"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=default / "results.json")
    parser.add_argument("--out", type=Path, default=default / "index.html")
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8"))
    args.out.write_text(render(data), encoding="utf-8")
    print(args.out)


if __name__ == "__main__":
    main()
