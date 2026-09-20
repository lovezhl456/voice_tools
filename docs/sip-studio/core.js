/* Pure scenario compiler. No network, DOM, native SIP or arbitrary plugin execution. */
(function (root) {
  'use strict';
  const get = (o, k, v) => (Object.hasOwn(o, k) ? o[k] : v);
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const uid = () =>
    's' + (globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2));
  const definitions = {
    wait: {
      name: '等待',
      icon: '◷',
      color: 'amber',
      hint: '给 IVR 留出播报时间',
      defaults: { seconds: 2 }
    },
    play: {
      name: '播放录音',
      icon: '▷',
      color: 'blue',
      hint: '8 kHz · 单声道 PCM16',
      defaults: { file: 'assets/question.wav' }
    },
    dtmf: {
      name: '发送按键',
      icon: '#',
      color: 'violet',
      hint: 'RFC 4733 / SIP INFO',
      defaults: { digits: '1#', method: 'rfc4733', duration_ms: 160, gap_ms: 100 }
    },
    play_media: {
      name: '回放 PCAP 媒体',
      icon: '≋',
      color: 'teal',
      hint: '引用转换后的 media.json',
      defaults: { file: 'media/media.json' }
    },
    hangup: { name: '挂断', icon: '↙', color: 'rose', hint: '结束通话并收尾', defaults: {} }
  };
  const makeStep = (action) => ({
    id: uid(),
    label: definitions[action].name,
    action,
    ...clone(definitions[action].defaults)
  });
  const defaultEnv = () => ({
    id: uid(),
    name: '本地回环',
    config: {
      target_uri: 'sip:peer@127.0.0.1:5070',
      account: { id_uri: 'sip:tester@127.0.0.1' },
      network: { sip_port: 0, rtp_port: 4000 },
      codec: 'PCMA',
      connect_timeout_s: 30,
      max_call_s: 120,
      record_early: false
    }
  });
  const makeCase = (envId, title = 'IVR 按键导航') => ({
    id: uid(),
    title,
    tags: 'IVR, 回归',
    envId,
    updated: new Date().toISOString(),
    steps: [
      { ...makeStep('wait'), label: '等待欢迎语', seconds: 2 },
      { ...makeStep('dtmf'), label: '选择业务菜单', digits: '1#' },
      { ...makeStep('wait'), label: '接收应答', seconds: 3 },
      makeStep('hangup')
    ]
  });
  function object(value, keys, label) {
    if (!value || typeof value !== 'object' || Array.isArray(value))
      throw Error(label + ' 必须是对象');
    const unknown = Object.keys(value).filter((k) => !keys.includes(k));
    if (unknown.length) throw Error(label + ' 含未知字段：' + unknown.join(', '));
  }
  function str(v, label, max = 256) {
    if (typeof v !== 'string' || !v || v.length > max || /[\x00-\x1f]/.test(v))
      throw Error(label + ' 必须是有效文本');
    return v;
  }
  function num(v, low, high, label, integer = false) {
    if (
      typeof v !== 'number' ||
      !Number.isFinite(v) ||
      v < low ||
      v > high ||
      (integer && !Number.isInteger(v))
    )
      throw Error(label + ` 须为 ${low}–${high}${integer ? ' 的整数' : ''}`);
    return v;
  }
  function uri(v, label, server = false) {
    str(v, label);
    const host = '(?:[A-Za-z0-9.-]+|\\[[0-9A-Fa-f:]+\\])';
    const user = server ? '' : "[A-Za-z0-9_.!~*'()%+\\-]+@";
    if (!new RegExp('^sip:' + user + host + '(?::[0-9]{1,5})?(?:;transport=udp)?$').test(v))
      throw Error(label + ' 仅支持 sip: 地址及 UDP');
    const port = v.match(/:(\d+)(?:;transport=udp)?$/);
    if (port) num(Number(port[1]), 1, 65535, label + ' 端口', true);
  }
  const stepFields = {
    wait: ['seconds'],
    play: ['file'],
    dtmf: ['digits', 'method', 'duration_ms', 'gap_ms'],
    play_media: ['file'],
    hangup: []
  };
  const assertionFields = {
    response_code: ['codes'],
    received_rtp: ['min_packets'],
    effective_audio: ['min_duration_s', 'threshold_dbfs'],
    dtmf: ['digits', 'match'],
    tone: ['frequencies_hz', 'min_duration_s', 'threshold_dbfs', 'min_power_ratio']
  };
  function validateAssertions(items) {
    if (!Array.isArray(items) || items.length > 64)
      throw Error('assertions 必须是最多 64 项的列表');
    const ids = new Set();
    items.forEach((a, i) => {
      if (!a || !Object.hasOwn(assertionFields, a.type)) throw Error('断言类型无效');
      object(a, ['id', 'type', ...assertionFields[a.type]], '断言');
      const id = get(a, 'id', 'assertion_' + (i + 1));
      str(id, '断言 ID', 128);
      if (ids.has(id)) throw Error('断言 ID 不得重复');
      ids.add(id);
      if (a.type === 'response_code') {
        if (!Array.isArray(a.codes) || !a.codes.length || a.codes.length > 500)
          throw Error('应答码列表无效');
        a.codes.forEach((v) => num(v, 200, 699, '应答码', true));
      } else if (a.type === 'received_rtp')
        num(get(a, 'min_packets', 1), 1, 100000000, '最少 RTP 包', true);
      else if (a.type === 'dtmf') {
        str(a.digits, '断言按键', 128);
        if (
          !/^[0-9*#ABCD]+$/.test(a.digits) ||
          !['exact', 'contains'].includes(get(a, 'match', 'exact'))
        )
          throw Error('按键断言无效');
      } else {
        num(a.min_duration_s, a.type === 'tone' ? 0.04 : 0.02, 900, '最短音频时长');
        num(get(a, 'threshold_dbfs', -40), -90, 0, '音频阈值');
        if (a.type === 'tone') {
          if (
            !Array.isArray(a.frequencies_hz) ||
            !a.frequencies_hz.length ||
            a.frequencies_hz.length > 4
          )
            throw Error('音调需要 1–4 个频率');
          a.frequencies_hz.forEach((v) => num(v, 100, 3500, '频率'));
          const fs = [...a.frequencies_hz].sort((a, b) => a - b);
          if (fs.some((v, i) => i && v - fs[i - 1] < 25)) throw Error('音调频率间隔至少 25 Hz');
          num(get(a, 'min_power_ratio', 0.6), 0.5, 1, '能量比例');
        }
      }
    });
    return items;
  }
  function validateScenario(data, assets = []) {
    const errors = [],
      warnings = [];
    let knownDuration = 0,
      unknownDuration = 0;
    const check = (fn) => {
      try {
        fn();
      } catch (e) {
        errors.push(e.message);
      }
    };
    check(() =>
      object(
        data,
        [
          'schema_version',
          'target_uri',
          'account',
          'network',
          'codec',
          'connect_timeout_s',
          'max_call_s',
          'record_early',
          'steps',
          'assertions'
        ],
        'scenario'
      )
    );
    if (errors.length) return { errors, warnings, knownDuration, unknownDuration };
    check(() => {
      if (data.schema_version !== '1.0') throw Error('scenario.schema_version 必须为 1.0');
    });
    check(() => uri(data.target_uri, '目标 URI'));
    check(() => validateAssertions(get(data, 'assertions', [])));
    check(() => {
      const a = get(data, 'account', {});
      object(a, ['id_uri', 'registrar_uri', 'proxy_uri', 'auth'], 'account');
      uri(get(a, 'id_uri', 'sip:voice-tools@127.0.0.1'), '本机 URI');
      ['registrar_uri', 'proxy_uri'].forEach((k) => {
        if (k in a) uri(a[k], k, true);
      });
      if ('auth' in a) {
        object(a.auth, ['username', 'realm', 'password_env'], 'auth');
        str(a.auth.username, '认证用户名');
        str(get(a.auth, 'realm', '*'), 'realm');
        str(a.auth.password_env, 'password_env');
        if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(a.auth.password_env))
          throw Error('password_env 只能填写环境变量名');
      }
    });
    check(() => {
      const n = get(data, 'network', {});
      object(n, ['sip_port', 'rtp_port', 'bind_address', 'public_address'], 'network');
      num(get(n, 'sip_port', 0), 0, 65535, 'SIP 端口', true);
      num(get(n, 'rtp_port', 4000), 1024, 65000, 'RTP 端口', true);
      if (get(n, 'rtp_port', 4000) % 2) throw Error('RTP 端口必须为偶数');
      ['bind_address', 'public_address'].forEach((k) => {
        if (
          k in n &&
          (typeof n[k] !== 'string' ||
            !/^\d{1,3}(\.\d{1,3}){3}$/.test(n[k]) ||
            n[k].split('.').some((v) => Number(v) > 255 || (v.length > 1 && v[0] === '0')))
        )
          throw Error(k + ' 必须为 IPv4 地址');
      });
    });
    check(() => {
      if (!['PCMA', 'PCMU'].includes(get(data, 'codec', 'PCMA'))) throw Error('仅支持 PCMA / PCMU');
      num(get(data, 'connect_timeout_s', 30), 1, 120, '接通超时');
      num(get(data, 'max_call_s', 120), 1, 900, '最长通话');
      if ('record_early' in data && typeof data.record_early !== 'boolean')
        throw Error('record_early 必须为布尔值');
    });
    if (!Array.isArray(data.steps) || data.steps.length < 1 || data.steps.length > 256) {
      errors.push('用例须包含 1–256 个步骤');
      return { errors, warnings, knownDuration, unknownDuration };
    }
    data.steps.forEach((step, i) =>
      check(() => {
        const label = `步骤 ${i + 1}`;
        if (!step || typeof step.action !== 'string' || !Object.hasOwn(stepFields, step.action))
          throw Error(label + ' 不支持的 action');
        object(step, ['action', ...stepFields[step.action]], label);
        if (step.action === 'wait') knownDuration += num(step.seconds, 0, 900, label + ' 等待时间');
        if (step.action === 'dtmf') {
          str(step.digits, label + ' 按键', 128);
          if (!/^[0-9*#ABCD]+$/.test(step.digits))
            throw Error(label + ' 按键只能使用 0–9、*、#、A–D');
          if (!['rfc4733', 'sip_info'].includes(get(step, 'method', 'rfc4733')))
            throw Error(label + ' 按键方式无效');
          knownDuration +=
            (step.digits.length *
              (num(get(step, 'duration_ms', 160), 40, 2000, label + ' 持续时间', true) +
                num(get(step, 'gap_ms', 100), 40, 2000, label + ' 间隔', true))) /
            1000;
        }
        if (['play', 'play_media'].includes(step.action)) {
          str(step.file, label + ' 素材路径', 4096);
          const asset = assets.find(
            (a) => a.path === step.file && a.kind === (step.action === 'play' ? 'wav' : 'media')
          );
          if (asset && Number.isFinite(asset.duration) && asset.duration > 0)
            knownDuration += asset.duration;
          else unknownDuration++;
          warnings.push(
            `${label}：执行前须由 CLI 校验 ${step.file} 的文件${step.action === 'play_media' ? '及音频哈希' : ''}。`
          );
        }
        if (step.action === 'hangup' && i !== data.steps.length - 1)
          throw Error('挂断必须是最后一个步骤');
      })
    );
    if (knownDuration > get(data, 'max_call_s', 120)) errors.push('已知动作时长超过最长通话时间');
    if (unknownDuration)
      warnings.push(`${unknownDuration} 个素材时长待确认，完整预算须由 CLI 校验。`);
    return { errors, warnings, knownDuration, unknownDuration };
  }
  function compile(item, env) {
    if (!env) throw Error('请选择有效环境');
    return {
      schema_version: '1.0',
      ...clone(env.config),
      ...(item.assertions?.length ? { assertions: clone(item.assertions) } : {}),
      steps: item.steps.map((s) => {
        if (!Object.hasOwn(stepFields, s.action)) throw Error('不支持的步骤：' + s.action);
        const out = { action: s.action };
        stepFields[s.action].forEach((k) => {
          if (k in s) out[k] = s[k];
        });
        return out;
      })
    };
  }
  function importScenario(data, title = '导入的用例') {
    const result = validateScenario(data);
    if (result.errors.length) throw Error(result.errors.join('；'));
    const env = defaultEnv();
    env.name = '导入环境';
    const { schema_version, steps, assertions, ...config } = data;
    env.config = {
      ...env.config,
      account: { id_uri: 'sip:voice-tools@127.0.0.1' },
      ...clone(config)
    };
    if (config.account)
      env.config.account = { id_uri: 'sip:voice-tools@127.0.0.1', ...config.account };
    if (config.network) env.config.network = { sip_port: 0, rtp_port: 4000, ...config.network };
    const item = makeCase(env.id, title);
    item.tags = '导入';
    item.assertions = clone(assertions || []);
    item.steps = steps.map((s) => ({ ...makeStep(s.action), ...clone(s) }));
    return { item, env };
  }
  function exportDocument(item, env) {
    return {
      studio_version: '1.0',
      title: item.title,
      tags: item.tags,
      environment: { name: env.name, config: clone(env.config) },
      steps: clone(item.steps),
      assertions: clone(item.assertions || [])
    };
  }
  function importDocument(data) {
    if (data?.schema_version) return importScenario(data);
    object(
      data,
      ['studio_version', 'title', 'tags', 'environment', 'steps', 'assertions'],
      'Studio 用例'
    );
    if (data.studio_version !== '1.0') throw Error('不支持的 Studio 文档版本');
    str(data.title, '用例名称', 160);
    if (typeof data.tags !== 'string' || data.tags.length > 256) throw Error('标签无效');
    object(data.environment, ['name', 'config'], 'environment');
    object(
      data.environment.config,
      [
        'target_uri',
        'account',
        'network',
        'codec',
        'connect_timeout_s',
        'max_call_s',
        'record_early'
      ],
      '环境参数'
    );
    str(data.environment.name, '环境名称', 80);
    if (!Array.isArray(data.steps)) throw Error('steps 必须是数组');
    data.steps.forEach((s) => {
      if (!s || typeof s.action !== 'string' || !Object.hasOwn(stepFields, s.action))
        throw Error('不支持的 action');
      object(s, ['id', 'label', 'action', ...stepFields[s.action]], '步骤');
      str(s.label, '步骤名称', 160);
    });
    const temp = {
        title: data.title,
        tags: data.tags,
        steps: data.steps,
        assertions: data.assertions || []
      },
      env = { id: uid(), name: data.environment.name, config: clone(data.environment.config) };
    // Studio and CLI imports must share defaults used by the inspector and previews.
    const normalized = importScenario(compile(temp, env), data.title);
    normalized.env.name = data.environment.name;
    normalized.item.tags = data.tags;
    normalized.item.steps.forEach((step, i) => {
      step.label = data.steps[i].label;
    });
    return normalized;
  }
  function moveStep(steps, id, to) {
    const at = steps.findIndex((s) => s.id === id);
    if (at < 0 || steps[at].action === 'hangup') return steps;
    const next = clone(steps),
      [s] = next.splice(at, 1);
    const end = next.findIndex((s) => s.action === 'hangup');
    next.splice(Math.max(0, Math.min(to, end < 0 ? next.length : end)), 0, s);
    return next;
  }
  function wavInfo(buffer) {
    const v = new DataView(buffer),
      read = (p, n) => String.fromCharCode(...new Uint8Array(buffer, p, n));
    if (
      v.byteLength < 44 ||
      read(0, 4) !== 'RIFF' ||
      read(8, 4) !== 'WAVE' ||
      v.getUint32(4, true) + 8 > v.byteLength
    )
      throw Error('不是完整的 RIFF WAV');
    let format = null,
      audio = null;
    const end = v.getUint32(4, true) + 8;
    for (let p = 12; p + 8 <= end; ) {
      const name = read(p, 4),
        size = v.getUint32(p + 4, true),
        start = p + 8;
      if (start + size > end) throw Error('WAV 数据被截断');
      if (name === 'fmt ' && size >= 16)
        format = {
          type: v.getUint16(start, true),
          channels: v.getUint16(start + 2, true),
          rate: v.getUint32(start + 4, true),
          align: v.getUint16(start + 12, true),
          bits: v.getUint16(start + 14, true)
        };
      if (name === 'data') audio = { start, size };
      p = start + size + (size % 2);
    }
    if (
      !format ||
      !audio ||
      format.type !== 1 ||
      format.channels !== 1 ||
      format.rate !== 8000 ||
      format.bits !== 16 ||
      format.align !== 2
    )
      throw Error('仅接受 8 kHz、单声道 PCM16 WAV');
    const duration = audio.size / 16000;
    num(duration, 0.001, 900, 'WAV 时长');
    if (audio.size % 2) throw Error('WAV 样本不完整');
    const peaks = [];
    for (let i = 0; i < 96; i++) {
      let peak = 0;
      const start = Math.floor((i * audio.size) / 2 / 96),
        end = Math.floor(((i + 1) * audio.size) / 2 / 96);
      for (let n = start; n < end; n++)
        peak = Math.max(peak, Math.abs(v.getInt16(audio.start + n * 2, true)) / 32768);
      peaks.push(peak);
    }
    return { duration, peaks };
  }
  function mediaInfo(data) {
    object(
      data,
      ['schema_version', 'audio', 'audio_sha256', 'duration_s', 'dtmf', 'source', 'warnings'],
      'media'
    );
    if (data.schema_version !== '1.0') throw Error('media 版本须为 1.0');
    str(data.audio, 'audio 路径', 4096);
    if (!/^[a-f0-9]{64}$/.test(data.audio_sha256)) throw Error('media 缺少有效 SHA256');
    num(data.duration_s, 0.001, 900, 'media 时长');
    if (!Array.isArray(get(data, 'dtmf', [])) || get(data, 'dtmf', []).length > 256)
      throw Error('DTMF 事件列表无效');
    let end = -1;
    get(data, 'dtmf', []).forEach((e) => {
      object(e, ['at_s', 'digit', 'duration_ms', 'end_observed'], 'DTMF event');
      num(e.at_s, 0, data.duration_s, '事件时间');
      num(e.duration_ms, 40, 8000, '事件持续时间', true);
      if (typeof e.digit !== 'string' || !/^[0-9*#ABCD]$/.test(e.digit))
        throw Error('DTMF 事件按键无效');
      if ('end_observed' in e && typeof e.end_observed !== 'boolean')
        throw Error('end_observed 必须为布尔值');
      if (e.at_s + 1e-9 < end || e.at_s + e.duration_ms / 1000 > data.duration_s + 1 / 8000)
        throw Error('DTMF 事件重叠或超出音频');
      end = e.at_s + e.duration_ms / 1000;
    });
    return {
      duration: data.duration_s,
      eventCount: get(data, 'dtmf', []).length,
      audio: data.audio
    };
  }
  const api = {
    clone,
    uid,
    definitions,
    makeStep,
    defaultEnv,
    makeCase,
    compile,
    validateScenario,
    validateAssertions,
    importScenario,
    exportDocument,
    importDocument,
    moveStep,
    wavInfo,
    mediaInfo
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.SipStudio = api;
})(globalThis);
