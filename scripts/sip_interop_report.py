#!/usr/bin/env python3
"""Render a portable HTML case specification from retained acceptance evidence."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import shutil


def h(value):
    return html.escape(str(value), quote=True)


def link(path, label):
    return f'<a href="{h(path)}">{h(label)}</a>'


def describe(step):
    action=step['action']
    if action=='wait':return f"等待 {step['seconds']} 秒"
    if action=='play':return '播放 '+Path(step['file']).name
    if action=='play_media':return '播放 PCAP 导入的音频，并按时间表发送去重后的 RFC4733 事件'
    if action=='dtmf':return f"发送 {step['digits']}，方式 {step.get('method','rfc4733')}，每键 {step.get('duration_ms',160)} ms"
    return '正常挂断'


def main():
    ap=argparse.ArgumentParser()
    for name in ('pjsua','baresip','public','out'):ap.add_argument('--'+name,type=Path,required=True)
    ap.add_argument('--sources',type=Path,help='可选：上游来源快照和自测日志目录；缺失时明确标记未提供')
    args=ap.parse_args();root=Path(__file__).resolve().parents[1];out=args.out.resolve()
    out.mkdir(parents=True,exist_ok=False)
    selected=[];cards=[]
    phases={'pjsua':'pjsua 本地','baresip':'Baresip 独立互通','public':'公共服务'}
    statuses={'PASS':'通过','FAIL':'未通过','ERROR':'执行错误','BLOCKED_EXTERNAL':'外部阻塞'}
    for phase in phases:
        base=getattr(args,phase).resolve()
        for source in sorted(base.glob('*/acceptance.json')):
            r=json.loads(source.read_text());id=r['id'];selected.append(r)
            dst=out/'evidence'/id;shutil.copytree(source.parent,dst)
            prefix=f'evidence/{id}/';scenario=json.loads((source.parent/'scenario.json').read_text())
            rows=''.join(f'<tr><td>{h(a["name"])}</td><td class="{ "pass" if a["passed"] else "fail" }">{"通过" if a["passed"] else "未通过"}</td></tr>' for a in r['assertions'])
            resources=[link(prefix+'acceptance.json','完整断言 JSON'),link(prefix+'scenario.json','呼叫策略'),link(prefix+'command.json','实际命令'),link(prefix+'run/result.json','CLI 结果'),link(prefix+'run/events.jsonl','事件时间线')]
            for f,label in [('peer.log','对端 SIP 日志'),('peer-command.json','对端启动命令'),('dns.json','当次 DNS'),('source.pcap','输入 PCAP'),('bundle/media.json','导入媒体清单'),('peer-rx.wav','对端接收录音')]:
                if (source.parent/f).exists():resources.append(link(prefix+f,label))
            metrics=r.get('metrics',{}).get('rx',{});media=''
            if metrics:
                facts=f"{metrics['duration_s']:.2f} 秒 · RMS {metrics['rms']:.0f}"
                if 'correlation' in metrics:facts+=f" · 分段相关系数中位数 {metrics['correlation']:.3f} · 时间偏移变化 {metrics.get('offset_spread_s',0)*1000:.1f} ms"
                media=f'<p class="metric">接收录音：{h(facts)}</p><audio controls preload="none" src="{prefix}run/rx.wav">{link(prefix+"run/rx.wav","接收录音 WAV")}</audio>'
            precondition='本地测试账号，无需 REGISTER；仅 127.0.0.1；对端与客户端端口独立。' if phase!='public' else '当次 DNS UDP SRV 发现出站代理；未配置账号认证或注册；仅公开测试分机。'
            exp='预期拒接／超时／远端挂断，并检查 CLI 失败分类与清理；用例通过不表示呼叫成功。' if r['mode'] in ('busy','timeout','remote_bye','codec_mismatch') else '预期通话完成，并通过下表中的音频、按键和录音断言。'
            sources=' · '.join(link(u, u.split('/tests/')[-1] if '/tests/' in u else u.split('/test/')[-1] if '/test/' in u else '来源 '+str(i+1)) for i,u in enumerate(r['source_urls']))
            steps=''.join('<li>'+h(describe(s))+'</li>' for s in scenario['steps'])
            cards.append(f'''<details class="case" data-phase="{phase}" data-status="{r['status']}" data-search="{h(id+' '+r['title']+' '+r['method']+' '+r['codec']+(' DTMF' if r['digits'] else ''))}">
<summary><span class="case-id">{id}</span><span class="case-title">{h(r['title'])}<small>{phases[phase]} · {r['codec']}</small></span><span class="badge {r['status'].lower()}">{statuses[r['status']]}</span></summary>
<div class="case-body"><p><strong>前提：</strong>{precondition}</p><p><strong>目标：</strong><code>{h(scenario['target_uri'])}</code></p><p><strong>预期：</strong>{exp}</p>
<div class="case-grid"><div><h3>执行步骤</h3><ol>{steps}</ol></div><div><h3>逐项结果</h3><table><thead><tr><th>断言</th><th>结果</th></tr></thead><tbody>{rows}</tbody></table></div></div>
{media}<p class="evidence">{' · '.join(resources)}</p><p class="source"><strong>来源与改编依据：</strong>{sources}</p><p class="muted">执行：{h(r['started_at'])}（UTC） · CLI 退出码 {r.get('exit_code','—')}</p></div></details>''')
    counts=Counter(r['status'] for r in selected)
    sources_dir=args.sources or root/'.local/sip-interop-sources'
    if (sources_dir/'upstream-tests').is_dir():
        shutil.copytree(sources_dir/'upstream-tests',out/'upstream-tests')
    else:
        (out/'upstream-tests').mkdir()
        (out/'upstream-tests/manifest.json').write_text(json.dumps({'status':'not_provided','note':'上游快照未提供；源码链接见各用例，不代表执行过上游自测。'},ensure_ascii=False)+'\n')
    for f in ['baresip-upstream-selftest.log','baresip-upstream-selftest-final.log','sip-regression.log']:
        if (sources_dir/f).exists():shutil.copy2(sources_dir/f,out/f)
    upstream=(sources_dir/'baresip-upstream-selftest-final.log').read_text() if (sources_dir/'baresip-upstream-selftest-final.log').exists() else ''
    upstream_pass=upstream.count('[       OK ]')
    upstream_total=upstream.count('[ RUN      ]')
    upstream_status='通过' if upstream_pass==6 and 'test failed' not in upstream else '需查看日志'
    history=[]
    for folder in sorted((root/'outputs').glob('sip-interop-*-20260919')):
        if folder==out:continue
        records=[]
        for p in sorted(folder.glob('*/acceptance.json')):
            r=json.loads(p.read_text());records.append(r)
        if not records:continue
        target=out/'attempts'/folder.name;target.mkdir(parents=True)
        for r in records:
            # Retain failed probes with their raw evidence, not just a changed status.
            if r['status']!='PASS':shutil.copytree(folder/r['id'],target/r['id'])
        (target/'results.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n')
        ct=Counter(r['status'] for r in records)
        history.append(f'<tr><td>{link("attempts/"+folder.name+"/results.json",folder.name)}</td><td>{h(dict(ct))}</td></tr>')
    (out/'results.json').write_text(json.dumps(selected,ensure_ascii=False,indent=2)+'\n')
    doc=TEMPLATE.replace('__CARDS__','\n'.join(cards)).replace('__TOTAL__',str(len(selected))).replace('__PASS__',str(counts['PASS'])).replace('__OTHER__',str(len(selected)-counts['PASS']))
    doc=doc.replace('__UPSTREAM__',f'{upstream_pass}/{upstream_total} {upstream_status}').replace('__HISTORY__',''.join(history))
    for name,label in [('baresip-upstream-selftest-final.log','原始日志'),('sip-regression.log','SIP 回归测试日志'),('baresip-upstream-selftest.log','这里')]:
        if not (out/name).exists():
            doc=doc.replace(f'<a href="{name}">{label}</a>',f'{label}（未提供）')
    doc=doc.replace('__TIME__',datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))
    (out/'index.html').write_text(doc)
    manifest=[]
    for file in sorted(out.rglob('*')):
        if file.is_file():manifest.append(dict(path=str(file.relative_to(out)),bytes=file.stat().st_size,sha256=hashlib.sha256(file.read_bytes()).hexdigest()))
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(dict(report=str(out/'index.html'),cases=len(selected),counts=counts,upstream=upstream_status,files=len(manifest)),ensure_ascii=False))


TEMPLATE='''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,"><title>SIP 自动拨测 · 用例与实测证据</title>
<style>
:root{--ink:#19323c;--muted:#567078;--line:#d6e2df;--teal:#006f68;--paper:#f3f5ef;--bad:#a13c28}*{box-sizing:border-box}body{margin:0;color:var(--ink);background:var(--paper);font:16px/1.75 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}main{max-width:1120px;margin:auto;padding:28px 32px 72px}a{color:var(--teal);text-underline-offset:3px;overflow-wrap:anywhere}nav{display:flex;justify-content:space-between;gap:16px;font-size:13px;margin-bottom:50px}.eyebrow{font-size:12px;letter-spacing:2px;font-weight:700;color:var(--teal)}h1{font-size:clamp(30px,4.4vw,52px);line-height:1.2;letter-spacing:-1.5px;margin:16px 0 22px}h2{font-size:24px;line-height:1.4;margin:0 0 16px}h3{font-size:16px;margin:0 0 12px}p{margin:12px 0}.intro{max-width:830px;font-size:18px}.muted,small{color:var(--muted)}.stats{display:grid;grid-template-columns:repeat(4,1fr);border-top:1px solid var(--ink);border-bottom:1px solid var(--line);margin:30px 0}.stat{padding:20px 14px;border-right:1px solid var(--line)}.stat:first-child{padding-left:0}.stat:last-child{border:0}.stat strong{display:block;font-size:34px;line-height:1.3;font-weight:650}.stat span{font-size:13px}.overview{background:#e3efea;border-left:4px solid var(--teal);padding:18px 24px;margin:25px 0 36px}.phases{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:20px 0 35px}.phase{padding:22px;background:white;border:1px solid var(--line);border-radius:12px}.phase b{font-size:19px}.phase .num{display:block;color:var(--teal);font:13px ui-monospace,monospace;margin-bottom:8px}.phase p{font-size:14px}.panel{background:#fff;border:1px solid var(--line);border-radius:14px;padding:26px;margin:22px 0}.filters{display:flex;flex-wrap:wrap;gap:12px;align-items:end;margin:18px 0}.filters label{display:flex;flex-direction:column;gap:5px;font-size:12px;font-weight:600}.filters label:first-child{flex:1;min-width:180px}input,select,button{font:inherit;color:var(--ink);border:1px solid #acc5bf;border-radius:7px;padding:10px 12px;background:#fff;min-height:44px}input{width:100%;font-size:15px}select,button{font-size:14px}button{cursor:pointer}button:hover{background:#e3efea}button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid #55b9a8;outline-offset:3px}.case{margin:12px 0;border:1px solid var(--line);border-radius:10px;background:white;overflow:hidden}.case[hidden]{display:none}summary{display:flex;align-items:center;gap:18px;padding:18px 22px;cursor:pointer;list-style:none}summary::-webkit-details-marker{display:none}summary:after{content:'＋';color:var(--teal);font-size:21px}.case[open]>summary:after{content:'−'}.case[open]>summary{background:#eaf1ed;border-bottom:1px solid var(--line)}.case-id{font:14px ui-monospace,monospace;color:var(--teal);font-weight:bold}.case-title{flex:1;font-weight:650;line-height:1.45}.case-title small{display:block;font-weight:400;font-size:12px;margin-top:6px}.badge{font-size:12px;white-space:nowrap;border-radius:99px;padding:3px 10px;background:#eee}.badge.pass{background:#dbefe5;color:#075d42}.badge.fail,.badge.error{background:#f9e2d9;color:#923621}.badge.blocked_external{background:#fff0c9;color:#755a0f}.case-body{padding:18px 24px 26px;font-size:14px}.case-grid{display:grid;grid-template-columns:1fr 1.35fr;gap:28px;margin:20px 0}.case ol{padding-left:22px;margin-top:0}code{font:13px ui-monospace,monospace;overflow-wrap:anywhere;background:#f0f4f1;padding:2px 5px;border-radius:4px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f0f4f1;padding:14px;border-radius:8px}table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:8px 10px;border-bottom:1px solid #e2e9e5;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{color:var(--muted);font-weight:500}.pass{color:#00684a}.fail{color:var(--bad)}.metric{padding:10px 14px;background:#f3f6f2;border-radius:6px}audio{max-width:100%;width:440px;margin:5px 0 10px}.evidence{line-height:2}.source{border-top:1px solid var(--line);padding-top:14px;font-size:12px}.notes{font-size:14px}.notes li{margin-bottom:9px}.tablewrap{overflow-x:auto}.history summary{padding:0;display:list-item;list-style:disclosure-closed}.history summary:after{content:none}.history table{margin-top:15px}footer{color:var(--muted);font-size:12px;margin-top:35px}#visible-count{font-size:13px;color:var(--muted)}@media(max-width:700px){main{padding:18px 16px 40px}nav{margin-bottom:30px}.intro{font-size:16px}.stats{grid-template-columns:1fr 1fr}.stat{border-bottom:1px solid var(--line);padding:15px 10px!important}.stat:nth-child(2){border-right:0}.stat strong{font-size:28px}.phases{grid-template-columns:1fr;gap:10px}.phase{padding:16px}.panel{padding:18px}.case-grid{grid-template-columns:1fr;gap:18px}summary{padding:16px 12px;gap:9px}.case-title{font-size:14px}.badge{padding:2px 7px}.case-body{padding:14px}.filters{gap:10px}.filters label:first-child{flex-basis:100%}h2{font-size:22px}}@media print{nav,.filters,audio{display:none}.case{break-inside:avoid}.case-body{display:block}.stats{margin:15px 0}body{background:white}main{max-width:none}a{color:inherit}summary:after{display:none}}
</style></head><body><main>
<nav><a href="../">← voice_tools 工具集</a><span>验收记录 · 2026.09.19</span></nav>
<header><div class="eyebrow">VOICE TOOLS / SIP INTEROPERABILITY</div><h1>SIP 自动拨测<br>用例与实测证据</h1><p class="intro">从同栈快速检查，到 Baresip 独立互通，再到公网测试分机。每个结论都对应实际命令、录音或协议事件。</p></header>
<div class="stats"><div class="stat"><strong>__TOTAL__</strong><span>端到端用例</span></div><div class="stat"><strong class="pass">__PASS__</strong><span>最终通过</span></div><div class="stat"><strong>__OTHER__</strong><span>最终未通过／阻塞</span></div><div class="stat"><strong>3</strong><span>验证阶段</span></div></div>
<div class="overview"><strong>本次范围：真实 SIP UDP、G.711 和合成音频。</strong><p>公网实际拨打 SIP2SIP 4444／3333 与 IPTel echo／music。没有注册账号、付费或使用真实用户录音。通过只代表本次机器、网络和短通话场景的结果，不代表长期可用性、语音机器人语义正确率或并发容量。</p></div>
<section class="phases"><div class="phase"><span class="num">01 / SAME STACK</span><b>pjsua 快速检查</b><p>9 个用例：PCMA／PCMU、双向 WAV、两种按键、拒接、超时、提前挂断和 PCAP 导入。</p></div><div class="phase"><span class="num">02 / INDEPENDENT STACK</span><b>Baresip 独立互通</b><p>7 个用例：不同 SIP 实现之间的音频、按键回送、录音、编码拒绝与媒体复现。</p></div><div class="phase"><span class="num">03 / PUBLIC NETWORK</span><b>公网路径</b><p>4 通短时呼叫。回声检查已知信号是否返回；公告／音乐检查实际接收音频。</p></div></section>
<section class="panel"><h2>GitHub 用例如何转成验收</h2><div class="tablewrap"><table><thead><tr><th>上游测试</th><th>借鉴的检查方式</th><th>对应本次用例</th></tr></thead><tbody>
<tr><td><a href="https://github.com/pjsip/pjproject/blob/2.17/tests/pjsua/mod_call.py">PJSIP mod_call.py</a></td><td>接通与挂断状态；接收端核对重复按键 1122</td><td>P01–P05、P09</td></tr>
<tr><td><a href="https://github.com/pjsip/pjproject/blob/2.17/tests/pjsua/mod_media_playrec.py">PJSIP mod_media_playrec.py</a> / WAV 场景</td><td>实际录音与播放素材比对；本次改为适应抖动缓冲的分段信号断言</td><td>P01–P03、P09</td></tr>
<tr><td><a href="https://github.com/baresip/baresip/blob/f48c14bc35b55eb4443c627ef77f578a91e2a9ff/test/call.c">Baresip test/call.c</a></td><td>接听、按键计数、拒接、取消；扩展为 CLI 退出码和事件检查</td><td>P06–P07、B01–B04、B06</td></tr>
<tr><td><a href="https://github.com/baresip/baresip/blob/f48c14bc35b55eb4443c627ef77f578a91e2a9ff/test/play.c">Baresip test/play.c</a> / test/ausrc.c</td><td>验证真实音频样本、文件音源和接收录音</td><td>B01–B02、B05、B07</td></tr></tbody></table></div>
<p class="notes">20 个用例是针对 voice-sip 改编和扩展的验收，不冒充完整上游测试套件。另直接运行 Baresip 官方 6 项自测：<strong>__UPSTREAM__</strong>，包括 test_play、test_ausrc、test_call_answer、test_call_dtmf、test_call_reject、test_call_cancel。<a href="baresip-upstream-selftest-final.log">原始日志</a> · <a href="upstream-tests/manifest.json">固定版本来源与 SHA256</a> · <a href="sip-regression.log">SIP 回归测试日志</a></p></section>
<section id="cases"><h2>用例说明与结果</h2><p class="muted">点击用例展开前提、步骤、预期、逐项断言和可试听录音。拒接／超时类用例预期 CLI 失败，正确识别失败才算通过。</p>
<div class="filters"><label>搜索用例<input id="search" type="search" placeholder="例如：DTMF、PCAP、P01"></label><label>阶段<select id="phase"><option value="">全部阶段</option><option value="pjsua">pjsua 本地</option><option value="baresip">Baresip 独立互通</option><option value="public">公共服务</option></select></label><label>结果<select id="status"><option value="">全部结果</option><option value="PASS">通过</option><option value="FAIL">未通过</option><option value="ERROR">执行错误</option><option value="BLOCKED_EXTERNAL">外部阻塞</option></select></label><button id="expand" type="button">展开当前用例</button><button id="collapse" type="button">收起全部</button></div><p id="visible-count" aria-live="polite"></p>
__CARDS__</section>
<section class="panel notes"><h2>判定规则与证据边界</h2><ul><li><strong>音频内容：</strong>十段 100 ms 的已知合成信号，至少 8 段相关系数 ≥ 0.65，中位数 ≥ 0.85，匹配时间偏移变化 ≤ 120 ms；静音、错误信号、截断音频有反例测试。不是 PESQ／MOS，也不证明人声质量。</li><li><strong>公告／音乐：</strong>接收录音至少 1 秒且 PCM16 RMS &gt; 100。没有指定内容或转写断言，不宣称语义正确。</li><li><strong>DTMF：</strong>比较对端日志或回送事件中的完整字符序列，保留重复按键。没有把“本地发送成功”作为“对端识别成功”。B04 的输入是 SIP INFO，回送为 RFC4733。</li><li><strong>录音：</strong>rx.wav 是实际接收并解码的音频；peer-rx.wav 是对端接收音频。tx_source.wav 是本地发送源时间轴重建，不能单独证明对端收到。</li><li><strong>PCAP：</strong>本次 P09／B07 测的是“PCAP → WAV＋去重按键 → 新呼叫”。没有完成 SIPp raw socket 原包直放验收。</li><li><strong>范围：</strong>没有测试 REGISTER／Digest 公网认证、TLS／SRTP、ICE／TURN、真实网关、真实语音机器人菜单、并发或长时间稳定性。原有 localhost 测试中的认证／提前媒体不计入本次 20 项。</li></ul></section>
<section class="panel notes"><h2>调试与复测记录</h2><p>首轮发现两类测试问题：长窗口波形匹配会受抖动缓冲影响，循环提示音也需要限制匹配到同一播放周期；Baresip 需显式 net_interface=127.0.0.1，并为 echo 模式启用 call_accept=yes。修正的是测试断言和对端配置，没有把失败记录删除或改为通过。</p><p>Baresip 官方自测首次在 DTMF 用例因 ausine.so 搜索路径缺失中止；补齐模块链接后重新执行，原始失败日志保留在 <a href="baresip-upstream-selftest.log">这里</a>。页面主列表取最终执行结果；下表保留先前尝试。</p><details class="history"><summary>查看全部执行批次（含首轮失败）</summary><div class="tablewrap"><table><thead><tr><th>批次与 JSON</th><th>该批次状态</th></tr></thead><tbody>__HISTORY__</tbody></table></div></details></section>
<section class="panel notes"><h2>复现与维护</h2><p>在 voice_tools 项目环境中执行，每次使用新的输出目录：</p><pre><code>.venv/bin/python -m tests.sip.interop --phase pjsua --out outputs/NEW-pjsua
.venv/bin/python -m tests.sip.interop --phase baresip --out outputs/NEW-baresip
.venv/bin/python -m tests.sip.interop --phase public --out outputs/NEW-public</code></pre><p>public 会真实拨打测试分机；普通 unittest discovery 不会自动拨公网。单项复测可加 <code>--case P01</code> 等。二进制和最小模块的构建位置见项目文档 <code>docs/sip-interop.md</code>。</p><p><a href="results.json">下载完整结果 JSON</a> · <a href="manifest.json">证据文件 SHA256 清单</a></p></section>
<footer>生成于 __TIME__ · PJSIP 2.17 / Baresip 4.11.0 / libre 4.11.0 · 合成素材，无真实用户录音。报告及证据可作为目录整体离线查看。</footer>
</main><script>
const cases=[...document.querySelectorAll('.case')];const search=document.querySelector('#search');const phase=document.querySelector('#phase');const status=document.querySelector('#status');
function filter(){const q=search.value.trim().toLowerCase();let count=0;for(const item of cases){item.hidden=!!((phase.value&&item.dataset.phase!==phase.value)||(status.value&&item.dataset.status!==status.value)||(q&&!item.dataset.search.toLowerCase().includes(q)));if(!item.hidden)count++;}document.querySelector('#visible-count').textContent=`显示 ${count} / ${cases.length} 个用例`;}
search.addEventListener('input',filter);phase.addEventListener('change',filter);status.addEventListener('change',filter);document.querySelector('#expand').addEventListener('click',()=>cases.filter(x=>!x.hidden).forEach(x=>x.open=true));document.querySelector('#collapse').addEventListener('click',()=>cases.forEach(x=>x.open=false));filter();
</script></body></html>'''


if __name__=='__main__':main()
