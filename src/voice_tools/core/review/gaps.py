"""Gap presentation shared by local and trusted task review, with no detector dependency."""
from pathlib import Path
from .page import render_page

GAP_LABEL_FIELDS = ['schema_version', 'audio_sha256', 'fingerprint', 'gap_id', 'origin', 'type', 'start_s', 'end_s', 'decision', 'reviewer', 'reviewed_at', 'notes']

FORM = '''<form id="reviewForm" class="review-form"><h3>间隙人工标注</h3>
<label>人工判断<select id="decision"><option value="">未复核</option><option value="confirmed">确认异常</option><option value="normal">正常停顿</option><option value="excluded">业务排除</option><option value="uncertain">无法判断</option></select></label>
<label>复核人<input id="reviewer" maxlength="100" required></label><label>说明<textarea id="notes" maxlength="5000"></textarea></label>
<button id="saveLabel" type="submit">记下标注</button><button id="discard" type="button">放弃表单修改</button><button id="manual" type="button">将当前试听范围补标为漏检</button>
<p>人工补标独立保存，不改写自动候选。修改试听范围不会改变已有标注区间。</p>
<p>空格播放，左右方向键切换间隙。波形经过降采样，短断音请放大并结合试听判断。</p>
<p id="evidenceSummary"></p><details><summary>本区间的证据详情</summary><pre id="evidenceDetail" style="white-space:pre-wrap;overflow-wrap:anywhere"></pre></details></form>'''


def render_gap_review(path, records, summary, hide_paths=False, task_review=False):
    visible = []
    for r in records:
        copy = {k:r[k] for k in ("audio_sha256", "result", "waveform", "playback_sources", "error", "evidence") if k in r}
        copy["input"] = Path(r["input"]).name if hide_paths else r["input"]
        visible.append(copy)
    render_page(path, {"records": visible, "summary": summary, "fields": GAP_LABEL_FIELDS},
        Path(__file__).with_name("gaps.js").read_text(), FORM,
        {"应答机会": "输出间隙", "当前机会": "当前间隙", "录音质检": "输出间隙复核",
         "录音人工复核": "输出间隙人工复核", "听清这一轮，再留下判断": "复听输出间隙，保留人工判断",
         '<a id="clip" download>': '<a id="clip" href="#" download>',
         '<a id="clipMeta" download>': '<a id="clipMeta" href="#" download>',
         "下载原窗口片段": "下载当前试听片段", "下载片段时间映射": "下载片段时间映射",
         '<a href="report.html">查看完整静态报告</a>': '<a href="../../index.html" target="_top">返回任务复查</a>' if task_review else '<a href="report.html">查看完整静态报告</a>',
         "下载片段包含原窗口前 2 秒、后 1 秒，并保留原始声道。": "下载片段按当前试听范围生成，并保留原始声道。"})
