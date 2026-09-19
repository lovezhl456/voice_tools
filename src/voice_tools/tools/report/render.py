"""无需在线资源的可移动报告；外部文本全部转义。"""
import html


def esc(value):
    return html.escape(str(value), quote=True)


def number(value, suffix=""):
    return "未知" if value is None else f"{value:.2f}{suffix}"


def table(headers, rows):
    return '<div class="scroll"><table><thead><tr>' + ''.join(f'<th>{esc(h)}</th>' for h in headers) + \
        '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{esc(c)}</td>' for c in row) + '</tr>' for row in rows) + \
        '</tbody></table></div>'


def bullets(items):
    return '<ul>' + ''.join(f'<li>{esc(item)}</li>' for item in items) + '</ul>'


def wave_svg(wave, index):
    values = wave['channels'][index]
    width = max(1, len(values))
    lines = ' '.join(f'M{i}, {32 - high * 29:.2f} V{32 - low * 29:.2f}' for i, (low, high) in enumerate(values))
    return (f'<svg class="wave" viewBox="0 0 {width} 64" preserveAspectRatio="none" role="img" '
            f'aria-label="声道 {index} 波形"><path d="M0 32 H{width}" stroke="#c6d4de"/>'
            f'<path d="{lines}" stroke="#137c88" fill="none" stroke-width="1"/></svg>')


def packet_svg(stream):
    points = stream['packets_per_second']
    if not points:
        return ''
    max_t = max(1, points[-1][0] + 1)
    max_n = max(n for _, n in points)
    # Bound rendered path size even for long captures. Aggregate into 600 display bins.
    bins = {}
    for second, count in points:
        x = int(second / max_t * 600)
        bins[x] = max(bins.get(x, 0), count)
    lines = ' '.join(f'M{x} 50 V{50 - n / max_n * 45:.2f}' for x, n in sorted(bins.items()))
    return (f'<svg class="packets" viewBox="0 0 600 54" preserveAspectRatio="none" role="img" aria-label="每秒 RTP 包数，峰值 {max_n}">'
            f'<path d="{lines}" stroke="#4269bc" stroke-width="2"/></svg>'
            f'<p class="muted">相对该流首包：0–{max_t} 秒；每秒包数峰值 {max_n}，图中按显示区间取峰值。</p>')


def render(data):
    cards = []
    for i, item in enumerate(data['audio'], 1):
        body = f'<h3>{i:02d} · {esc(item["name"])}</h3>'
        if item['status'] == 'error':
            body += f'<p class="error">{esc(item["error"])}</p>'
        else:
            body += f'<p>{number(item["duration_s"], " 秒")} · {item["sample_rate"]} Hz · {item["channels"]} 声道</p>'
            health = item['health']
            body += table(['声道', '峰值 dBFS', '活动中位电平 dBFS', '低于门限比例', '削波样本比例'], [
                [c['channel'], c['peak_dbfs'], number(c['active_median_dbfs']),
                 number(c['silence_fraction'] * 100, '%'), number(c['clipping_fraction'] * 100, '%')]
                for c in health['channels']])
            if health['similarity_needs_review']:
                body += '<p class="notice">双声道高度相似或相同，需要核实录音路由；不能据此确认角色或串音。</p>'
            for c in health['channels']:
                body += f'<p class="muted">声道 {c["channel"]} · 时间从 0 至 {number(item["duration_s"], " 秒")}</p>'
                body += wave_svg(item['waveform'], c['channel'])
                spans = c['silence_spans']
                body += f'<details><summary>低于门限的片段（{len(spans)} 段）</summary>'
                body += table(['起点（秒）', '终点（秒）'], spans[:100])
                if len(spans) > 100:
                    body += '<p>页面显示前 100 段，完整结果见 report.json。</p>'
                body += '</details>'
            if item.get('playback'):
                body += f'<audio controls preload="metadata" src="{esc(item["playback"])}"></audio>'
            else:
                body += '<p class="muted">未复制录音；使用 --include-audio 可生成离线试听附件。</p>'
            body += '<p class="muted">声道编号从 0 开始，不自动指定用户/AI。低能量可能是正常停顿；能量活动也可能是噪声。</p>'
        body += f'<details><summary>输入与校验信息</summary><p class="mono">{esc(item["path"])}</p><p class="mono">SHA-256 {esc(item.get("sha256", "未知"))}</p></details>'
        cards.append('<article>' + body + '</article>')
    pcaps = []
    for i, item in enumerate(data['pcaps'], 1):
        body = f'<h3>{i:02d} · {esc(item["name"])}</h3>'
        if item['status'] == 'error':
            body += f'<p class="error">{esc(item["error"])}</p>'
        else:
            analysis = item['analysis']
            body += f'<p>检查 {analysis["packets_examined"]} 个包 · {len(analysis["streams"])} 条 RTP 流 · {analysis["truncated_packets"]} 个截断包</p>'
            body += bullets(analysis['warnings'])
            for stream in analysis['streams']:
                body += (f'<section class="stream"><h4 class="mono">{esc(stream["src"])}:{stream["src_port"]} → '
                         f'{esc(stream["dst"])}:{stream["dst_port"]}</h4>'
                         f'<p class="muted">SSRC {esc(stream["ssrc"])} · PT {esc(stream["payload_types"])} · 时钟 {esc(stream["clock_rate"] or "未知或变化")} Hz · '
                         f'首包 Unix UTC {number(stream["first_epoch"])}</p>')
                body += table(['包数', '序号缺口候选', '候选占比', '重复候选', '乱序', '序号大跳变'], [
                    [stream['packets'], stream['sequence_gap_candidates'], number(stream['gap_fraction'] * 100, '%'),
                     stream['duplicate_candidates'], stream['reordered_packets'], stream['sequence_discontinuities']]])
                body += table(['持续时间', '最大到达间隔', 'RFC3550 抖动末值', '平滑抖动峰值'], [
                    [number(stream['duration_s'], ' 秒'), number(stream['max_interarrival_ms'], ' ms'),
                     number(stream['jitter_last_ms'], ' ms'), number(stream['jitter_max_ms'], ' ms')]])
                body += packet_svg(stream) + '</section>'
            if analysis['tshark_warnings']:
                body += f'<details><summary>tshark 提示</summary><pre>{esc(analysis["tshark_warnings"])}</pre></details>'
        body += f'<details><summary>输入与校验信息</summary><p class="mono">{esc(item["path"])}</p><p class="mono">SHA-256 {esc(item.get("sha256", "未知"))}</p></details>'
        pcaps.append('<article>' + body + '</article>')
    captures = ''
    for item in data.get('sessions', []):
        captures += f'<article><h3>会话 · {esc(item["call_id"])}</h3>{bullets(item.get("warnings", []))}</article>'
    for item in data.get('correlations', []):
        captures += (f'<article><h3>HOMER 关联 · {esc(item["call_id"])}</h3>'
                     f'<p>本地观测 {esc(item["local_observations"])} 条；HOMER search 状态 {esc(item["search"]["status"])}，'
                     f'trace 状态 {esc(item["trace"]["status"])}。</p>'
                     f'<p>HOMER 返回的其他 Call-ID：{esc(item.get("related_call_ids_from_homer", []))}</p>'
                     f'{bullets(item.get("warnings", []))}</article>')
    for item in data['captures']:
        stats = item.get('tcpdump_stats', {})
        captures += (f'<article><h3>抓包来源 · {esc(item["host"])}</h3><p>状态 {esc(item["status"])}</p>'
                     f'<p>tcpdump 捕获 {esc(stats.get("captured") if stats.get("captured") is not None else "未知")} 包；'
                     f'内核丢弃 {esc(stats.get("dropped_by_kernel") if stats.get("dropped_by_kernel") is not None else "未知")} 包。</p>'
                     f'<p class="mono">{esc(item["bpf"] or "恢复取回，原端点未知")}</p>{bullets(item["warnings"])}</article>')
    status = '包含错误或部分统计' if data['partial'] else '分析完成'
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>''' + esc(data['title']) + '''</title><style>
:root{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#20333e;background:#f2f5f7;line-height:1.65}
*{box-sizing:border-box}body{margin:0}header,main,footer{max-width:1160px;margin:auto;padding:28px}header{padding-top:44px}
h1{font-size:clamp(27px,4vw,40px);margin:8px 0 12px;line-height:1.25}h2{font-size:25px;margin:35px 0 12px}h3{margin:0 0 12px;overflow-wrap:anywhere}h4{margin:0 0 6px}
.eyebrow{font-size:12px;letter-spacing:.12em;color:#137c88;font-weight:700}.muted{color:#62717b;font-size:13px}.notice{background:#fff3d8;border-left:4px solid #d4a331;padding:14px 18px;border-radius:4px}
.stats{display:flex;gap:12px;flex-wrap:wrap;margin:20px 0}.stats span{background:white;border:1px solid #d9e3e9;padding:10px 18px;border-radius:9px}nav{display:flex;gap:20px;flex-wrap:wrap}a{color:#075f79;text-underline-offset:3px}
article{background:#fff;border:1px solid #d9e3e9;border-radius:12px;padding:24px;margin:16px 0;min-width:0}.stream{border-top:1px solid #e0e7ec;padding-top:20px;margin-top:20px}p,li{overflow-wrap:anywhere}li{margin:6px 0}
.scroll{overflow-x:auto;max-width:100%;margin:12px 0}table{border-collapse:collapse;width:100%;font-size:13px;text-align:left}th,td{padding:11px 13px;border-bottom:1px solid #e5ebef;white-space:nowrap}th{background:#f5f8fa;color:#516673}details{margin-top:14px}summary{cursor:pointer;color:#075f79}pre{white-space:pre-wrap;overflow-wrap:anywhere}.mono{font-family:ui-monospace,monospace;font-size:12px;overflow-wrap:anywhere}.error{color:#ac3030;background:#fff2f0;padding:14px;border-radius:7px}
.wave,.packets{display:block;width:100%;height:80px;background:#f6fafb;border-radius:4px}audio{width:100%;margin-top:20px}footer{font-size:12px;color:#62717b;padding-bottom:45px}
@media(max-width:600px){header,main,footer{padding:18px}header{padding-top:28px}article{padding:16px}.stats{gap:7px}.stats span{padding:8px 11px;font-size:13px}h2{font-size:22px}ul{padding-left:20px}}
</style><header><div class="eyebrow">VOICE TOOLS / MEDIA EVIDENCE</div><h1>''' + esc(data['title']) + \
        f'''</h1><p class="muted">{esc(data['created_at'])} · {status} · CPU / 离线分析</p>
<div class="stats"><span>录音 <b>{len(data['audio'])}</b></span><span>PCAP <b>{len(data['pcaps'])}</b></span><span>错误 <b>{data['errors']}</b></span></div>
<p class="notice">{esc(data['conclusion'])}</p><nav><a href="#audio">录音分析</a><a href="#network">网络分析</a><a href="report.json">完整 JSON</a></nav></header>
<main>{captures}<h2 id="audio">录音分析</h2><p class="muted">20 ms 分帧，活动门限 {data['parameters']['threshold_db']} dBFS。各文件时间从自身起点计算。</p>
{''.join(cards) or '<p>未提供录音。</p>'}<h2 id="network">网络分析</h2>{''.join(pcaps) or '<p>未提供 PCAP。</p>'}
<article><h3>如何解释这些结果</h3><p>录音低能量、RTP 序号缺口和到达间隔异常是不同观测。单份 PCAP 无法测出单向网络延迟，缺少起始时间映射的录音不能与网络事件自动对齐。仅凭这些指标不能确认用户/AI 角色、LLM/TTS 故障或运营商转码根因。</p><p>序号相邻差超过 3000 时分段计数并标记大跳变；该处缺失量未知。动态 PT 的时钟由 --clock-rate 明确指定，不能把音频采样率直接当作所有编码的 RTP 时钟。</p></article>
</main><footer>voice-tools {esc(data['tool_version'])} · 输入摘要及参数见 report.json。报告可能包含 IP、路径和录音附件，请按原证据权限保存。</footer></html>'''
