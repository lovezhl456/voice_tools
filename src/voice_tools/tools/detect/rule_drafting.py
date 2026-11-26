"""Send only rule discussion and the local capability catalog to OpenAI."""
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .definition import parse_document
from .metrics import catalog
from .timeline_rules import KINDS, validate

ENDPOINT = 'https://api.openai.com/v1/responses'
FORMAT = {
    'type': 'json_schema', 'name': 'recording_rule_draft', 'strict': True,
    'schema': {'type': 'object', 'additionalProperties': False,
               'properties': {key: {'type': 'string'} for key in ('explanation', 'question', 'rule_json')},
               'required': ['explanation', 'question', 'rule_json']},
}
INSTRUCTIONS = """你帮助用户定义本地录音质检规则。只用已支持的检测能力，不执行规则。
explanation 用中文说明适用声道、证据、条件和实际标注范围。
有影响判断的歧义时在 question 只问一个问题，并将 rule_json 留空。
能力不支持时说明缺少的能力，将 rule_json 留空；不要用不等价的声学指标替代。
可执行时 rule_json 是 JSON 对象序列化后的字符串，question 留空。沿用用户所选规则 ID，修改版本号。
时序格式示例 {"schema_version":"2.0","id":"reply-wait","version":"1","name":"应答等待",
"kind":"response_wait","channel":"both","op":"gt","min_s":2,"threshold_dbfs":-45}。
仅静音 quiet 支持 left、right、both。其他 kind 的 channel 固定为 both。
response_wait 的阈值范围 -90 至 0 dBFS，依赖用户和 AI 角色映射，以新起声为终点，未配对单列。
output_pause 以一条 AI 声道的两个活动片段为边界，属于声学候选。
窗口格式沿用 detect 1.0，示例 {"schema_version":"1.0","id":"level","version":"1","name":"低音量",
"window":{"kind":"sliding","length_s":2,"step_s":2},"metrics":{"level":{"kind":"rms_dbfs","channel":1}},
"rules":[{"id":"low","label":"低音量","when":{"metric":"level","op":"lt","value":-35}}]}。
窗口类型 whole / sliding / after_activity。声道 0 左、1 右。
条件支持 all、any、not 和叶节点 metric/op/value，比较 lt、le、gt、ge、eq、ne。
只把章节末尾的能力目录用作受支持参数的依据。
"""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def draft(data):
    raw_key = data.get('key') or os.getenv('OPENAI_API_KEY', '')
    if not isinstance(raw_key, str) or not 10 <= len(raw_key) <= 512 or not raw_key.isascii() or any(ch.isspace() for ch in raw_key):
        raise ValueError('请输入有效的 GPT API Key，或在本机服务设置 OPENAI_API_KEY')
    model = data.get('model') or os.getenv('VOICE_TOOLS_OPENAI_MODEL', 'gpt-4o-mini')
    if not isinstance(model, str) or not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,100}', model):
        raise ValueError('模型 ID 无效')
    messages = data['messages']
    if not isinstance(messages, list) or not 1 <= len(messages) <= 20:
        raise ValueError('请提供 1–20 条规则讨论消息')
    for message in messages:
        if (not isinstance(message, dict) or set(message) != {'role', 'content'}
                or message['role'] not in ('user', 'assistant')
                or not isinstance(message['content'], str) or not 1 <= len(message['content']) <= 6000):
            raise ValueError('规则讨论消息格式无效或过长')
    payload = dict(model=model, input=messages, store=False, max_output_tokens=4000,
                   text={'format': FORMAT}, instructions=INSTRUCTIONS + json.dumps(
                       {'timing': KINDS, 'metrics': catalog()}, ensure_ascii=False))
    request = Request(ENDPOINT, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                      headers={'Authorization': 'Bearer ' + raw_key, 'Content-Type': 'application/json'})
    try:
        with build_opener(NoRedirect()).open(request, timeout=55) as response:
            reply = json.loads(response.read(1024 * 1024))
    except HTTPError as error:
        raise ValueError(f'OpenAI 返回 HTTP {error.code}，请检查模型、Key 或稍后重试') from None
    except (URLError, OSError, ValueError, UnicodeError):
        raise ValueError('连接 OpenAI 或读取响应失败，请稍后重试') from None
    if reply.get('status') != 'completed':
        raise ValueError('AI 草稿未完整生成，请缩短需求后重试')
    text_parts = [part['text'] for item in reply.get('output', []) if item.get('type') == 'message'
                  for part in item.get('content', []) if part.get('type') == 'output_text']
    if not text_parts:
        raise ValueError('AI 未返回可读取的规则草稿')
    try:
        result = parse_document(''.join(text_parts))
        result['config'] = validate(parse_document(result['rule_json'])) if result['rule_json'] else None
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError('AI 草稿未通过本地规则校验：' + str(error)) from None
    return {key: result[key] for key in ('config', 'explanation', 'question')}
