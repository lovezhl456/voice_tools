/* Portable queue and SIPp package compiler. No network or execution. */
(function (root) {
  'use strict';
  const C = typeof module !== 'undefined' && module.exports ? require('./core.js') : root.SipStudio;
  const integer = (v, min, max, label) => {
    if (!Number.isInteger(v) || v < min || v > max)
      throw Error(`${label} 须为 ${min}–${max} 的整数`);
    return v;
  };
  function queue(jobs, options = {}) {
    if (!Array.isArray(jobs) || jobs.length < 1 || jobs.length > 100)
      throw Error('队列须包含 1–100 项');
    const concurrency = integer(options.concurrency ?? 1, 1, 32, '并发上限'),
      sip = integer(options.sip_port_base ?? 0, 0, 65535, 'SIP 起始端口'),
      rtp = integer(options.rtp_port_base ?? 4000, 1024, 65000, 'RTP 起始端口');
    if (
      (sip && sip < 1024) ||
      rtp % 2 ||
      rtp + 4 * (concurrency - 1) > 65000 ||
      rtp + 4 * concurrency - 1 > 65535 ||
      sip + concurrency - 1 > 65535
    )
      throw Error('端口池无效或越界；RTP 起始端口须为偶数');
    if (sip && sip < rtp + 4 * concurrency && sip + concurrency > rtp)
      throw Error('SIP 与 RTP/RTCP 端口池不能重叠');
    let count = 0;
    const entries = jobs.map((j) => {
      const errors = C.validateScenario(j.scenario).errors;
      if (errors.length) throw Error(errors[0]);
      if (
        typeof j.name !== 'string' ||
        !j.name.trim() ||
        j.name.length > 160 ||
        /[\x00-\x1f]/.test(j.name)
      )
        throw Error('用例名称无效');
      count += integer(j.repeat, 1, 1000, '重复次数');
      return { name: j.name, repeat: j.repeat, scenario: C.clone(j.scenario) };
    });
    if (count > 10000) throw Error('展开后的队列最多 10000 通');
    return {
      schema_version: '1.0',
      kind: 'sip_batch',
      concurrency,
      sip_port_base: sip,
      rtp_port_base: rtp,
      jobs: entries
    };
  }
  function request(scenario, options = {}) {
    if (scenario?.assertions && Object.keys(scenario.assertions).length)
      throw Error('SIPp 压力导出不执行功能断言，请使用功能批量');
    const normalized = C.importScenario(scenario);
    scenario = C.compile(normalized.item, normalized.env);
    if (['auth', 'registrar_uri', 'proxy_uri'].some((k) => k in scenario.account))
      throw Error('SIPp 暂不支持认证、REGISTER 或代理；请使用功能批量');
    if (scenario.account.id_uri.includes('[')) throw Error('SIPp 压力主叫地址暂不支持 IPv6');
    if (scenario.record_early)
      throw Error('SIPp 不提供接收录音；请关闭 early media 录音或使用功能批量');
    if (scenario.steps.some((s) => !['wait', 'dtmf', 'hangup'].includes(s.action)))
      throw Error('SIPp 导出支持等待、DTMF、挂断；WAV / PCAP 媒体请使用功能批量或 CLI 单流回放');
    const ipv4 = (v) =>
      typeof v === 'string' &&
      /^(0|[1-9]\d{0,2})(\.(0|[1-9]\d{0,2})){3}$/.test(v) &&
      v.split('.').every((n) => +n < 256);
    let remote;
    let targets = options.targets ?? [scenario.target_uri];
    if (!Array.isArray(targets) || !targets.length || targets.length > 1000)
      throw Error('目标须为 1–1000 个 SIP URI');
    targets = targets.map((target) => {
      const s = { ...scenario, target_uri: target };
      const errors = C.validateScenario(s).errors;
      if (errors.length) throw Error(errors[0]);
      const address = target.split('@')[1].replace(/;transport=udp$/, ''),
        [host, port = '5060'] = address.split(':');
      if (!ipv4(host)) throw Error('SIPp 目标须为 IPv4 地址');
      const endpoint = `${host}:${Number(port)}`;
      if (remote && endpoint !== remote) throw Error('同一个 SIPp 包只能使用相同 IPv4 网关和端口');
      remote = endpoint;
      return target.replace(/;transport=udp$/, '');
    });
    const local_ip = options.local_ip ?? '127.0.0.1';
    if (!ipv4(local_ip)) throw Error('本机地址须为 IPv4');
    const calls = integer(options.calls ?? 10, 1, 1000000, '总呼叫数'),
      concurrency = integer(options.concurrency ?? 1, 1, 10000, '并发上限'),
      sip_port = integer(options.sip_port ?? 5062, 1024, 65535, 'SIP 端口'),
      rtp_port = integer(options.rtp_port ?? 6000, 1024, 65000, 'RTP 端口'),
      timeout_s = integer(options.timeout_s ?? 300, 1, 86400, '总时限');
    const rate = options.rate ?? 1;
    if (typeof rate !== 'number' || !Number.isFinite(rate) || rate < 0.01 || rate > 10000)
      throw Error('呼叫速率须为 0.01–10000 CPS');
    if (rtp_port % 2 || [rtp_port, rtp_port + 1].includes(sip_port))
      throw Error('RTP 端口须为偶数，且不能与 SIP/RTCP 重叠');
    return {
      schema_version: '1.0',
      kind: 'sipp_load',
      scenario,
      targets,
      calls,
      concurrency,
      rate,
      local_ip,
      sip_port,
      rtp_port,
      timeout_s
    };
  }
  function xml(s) {
    const rfc = s.steps.some((x) => x.action === 'dtmf' && x.method === 'rfc4733'),
      pt = s.codec === 'PCMA' ? 8 : 0,
      header = `Via: SIP/2.0/UDP [local_ip]:[local_port];branch=[branch]\nFrom: <${s.account.id_uri}>;tag=[call_number]\n`;
    const send = (message, retrans = false) =>
      `<send${retrans ? ' retrans="500"' : ''}><![CDATA[\n${message}\n]]></send>`;
    const dialog = (method, cseq, body = '') =>
      `${method} [next_url] SIP/2.0\n${header}[last_To:]\nCall-ID: [call_id]\nCSeq: ${cseq} ${method}\n[routes]\nMax-Forwards: 70\n${body ? 'Content-Type: application/dtmf-relay\n' : ''}Content-Length: [len]\n\n${body}`;
    const invite = `INVITE [field0] SIP/2.0\n${header}To: <[field0]>\nCall-ID: [call_id]\nCSeq: 1 INVITE\nContact: <sip:voice-tools@[local_ip]:[local_port]>\nMax-Forwards: 70\nContent-Type: application/sdp\nContent-Length: [len]\n\nv=0\no=voice-tools [call_number] 1 IN IP4 [local_ip]\ns=voice-tools-load\nc=IN IP4 [media_ip]\nt=0 0\nm=audio [media_port] RTP/AVP ${pt}${rfc ? ' 101' : ''}\na=rtpmap:${pt} ${s.codec}/8000${rfc ? '\na=rtpmap:101 telephone-event/8000\na=fmtp:101 0-15' : ''}\na=sendrecv\n`;
    const timeout = Math.ceil(s.connect_timeout_s * 1000),
      parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<scenario name="voice-tools load">',
        send(invite, true),
        '<recv response="100" optional="true"/>',
        '<recv response="180" optional="true"/>',
        '<recv response="183" optional="true"/>',
        `<recv response="200" rrs="true" rtd="true" timeout="${timeout}"/>`,
        send(dialog('ACK', 1))
      ];
    let seq = 2;
    s.steps.forEach((step, index) => {
      if (step.action === 'wait' && step.seconds)
        parts.push(`<pause milliseconds="${Math.ceil(step.seconds * 1000)}"/>`);
      else if (step.action === 'dtmf') {
        if (step.method === 'rfc4733')
          parts.push(
            `<nop><action><exec play_pcap_audio="dtmf-${index}.pcap"/></action></nop>`,
            `<pause milliseconds="${step.digits.length * (step.duration_ms + step.gap_ms)}"/>`
          );
        else
          for (const digit of step.digits)
            parts.push(
              send(dialog('INFO', seq++, `Signal=${digit}\nDuration=${step.duration_ms}\n`), true),
              `<recv response="200" timeout="${timeout}"/>`,
              `<pause milliseconds="${step.duration_ms + step.gap_ms}"/>`
            );
      }
    });
    parts.push(
      send(dialog('BYE', seq), true),
      `<recv response="200" timeout="${timeout}"/>`,
      '<ResponseTimeRepartition value="10,20,50,100,150,200,500,1000"/>',
      '</scenario>'
    );
    return (
      parts.join('\n').replace('<send retrans="500">', '<send retrans="500" start_rtd="true">') +
      '\n'
    );
  }
  function dtmfPcap(step) {
    const chunks = [],
      head = new Uint8Array(24),
      h = new DataView(head.buffer);
    h.setUint32(0, 0xa1b2c3d4, true);
    h.setUint16(4, 2, true);
    h.setUint16(6, 4, true);
    h.setUint32(16, 65535, true);
    h.setUint32(20, 1, true);
    chunks.push(head);
    let seq = 0;
    [...step.digits].forEach((digit, i) => {
      const start = i * (step.duration_ms + step.gap_ms),
        d = step.duration_ms,
        events = [];
      for (let ms = 0; ms < d; ms += 20) events.push([ms, false]);
      for (let n = 0; n < 3; n++) events.push([d + n, true]);
      for (const [ms, end] of events) {
        const frame = new Uint8Array(58),
          v = new DataView(frame.buffer);
        v.setUint16(12, 0x0800);
        v.setUint8(14, 0x45);
        v.setUint16(16, 44);
        v.setUint16(18, seq % 65536);
        v.setUint8(22, 64);
        v.setUint8(23, 17);
        v.setUint32(26, 0x7f000001);
        v.setUint32(30, 0x7f000001);
        let sum = 0;
        for (let n = 14; n < 34; n += 2) sum += v.getUint16(n);
        while (sum >> 16) sum = (sum & 65535) + (sum >> 16);
        v.setUint16(24, ~sum & 65535);
        v.setUint16(34, 6000);
        v.setUint16(36, 6002);
        v.setUint16(38, 24);
        v.setUint8(42, 0x80);
        v.setUint8(43, 101 | (ms === 0 ? 0x80 : 0));
        v.setUint16(44, seq++ % 65536);
        v.setUint32(46, start * 8);
        v.setUint32(50, 0x56544c31);
        v.setUint8(54, '0123456789*#ABCD'.indexOf(digit));
        v.setUint8(55, end ? 0x8a : 10);
        v.setUint16(56, Math.min(ms + 20, d) * 8);
        const rec = new Uint8Array(16),
          r = new DataView(rec.buffer),
          stamp = start + ms;
        r.setUint32(0, Math.floor(stamp / 1000), true);
        r.setUint32(4, (stamp % 1000) * 1000, true);
        r.setUint32(8, 58, true);
        r.setUint32(12, 58, true);
        chunks.push(rec, frame);
      }
    });
    return concat(chunks);
  }
  const concat = (chunks) => {
    const data = new Uint8Array(chunks.reduce((s, b) => s + b.length, 0));
    let i = 0;
    for (const c of chunks) {
      data.set(c, i);
      i += c.length;
    }
    return data;
  };
  function files(config) {
    const r = request(config.scenario, config),
      remote = r.targets[0].split('@')[1],
      address = remote.includes(':') ? remote : remote + ':5060';
    const command = [
      'sipp',
      address,
      '-sf',
      'scenario.xml',
      '-inf',
      'targets.csv',
      '-i',
      r.local_ip,
      '-p',
      r.sip_port,
      '-mi',
      r.local_ip,
      '-mp',
      r.rtp_port,
      '-t',
      'u1',
      '-r',
      r.rate,
      '-rp',
      1000,
      '-l',
      r.concurrency,
      '-m',
      r.calls,
      '-timeout',
      r.timeout_s + 's',
      '-timeout_error',
      '-nostdin',
      '-trace_stat',
      '-stf',
      'statistics.csv',
      '-fd',
      1,
      '-trace_err',
      '-error_file',
      'errors.log'
    ].join(' ');
    const result = {
      'load.json': JSON.stringify(r, null, 2) + '\n',
      'scenario.xml': xml(r.scenario),
      'targets.csv': 'SEQUENTIAL\n' + r.targets.join('\n') + '\n',
      'run.sh': '#!/bin/sh\nset -eu\ncd -- "$(dirname -- "$0")"\nexec ' + command + '\n',
      'README.txt':
        'SIPp 独立压力包。先检查 load.json 中的地址、并发、速率和总量。\n离线校验：voice-tools sip sipp-load . --dry-run --out ../load-plan-001\n执行并留存结果：voice-tools sip sipp-load . --out ../load-run-001\n直接执行：sh run.sh（统计留在包目录，无 voice_tools result.json）\nRFC4733 需要 SIPp PCAP 支持，telephone-event PT 固定 101；macOS 可能限制原始发包。\n此包不发送连续语音 RTP，不生成接收 WAV，也不判断业务内容。\n'
    };
    r.scenario.steps.forEach((s, i) => {
      if (s.action === 'dtmf' && s.method === 'rfc4733') result[`dtmf-${i}.pcap`] = dtmfPcap(s);
    });
    return result;
  }
  function zip(files) {
    const encode = new TextEncoder(),
      locals = [],
      central = [];
    let offset = 0,
      count = 0;
    const crc = (b) => {
      let c = 0xffffffff;
      for (const n of b) {
        c ^= n;
        for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (c & 1 ? 0xedb88320 : 0);
      }
      return (c ^ 0xffffffff) >>> 0;
    };
    for (const [name, value] of Object.entries(files)) {
      const n = encode.encode(name),
        b = typeof value === 'string' ? encode.encode(value) : value,
        check = crc(b),
        l = new Uint8Array(30),
        lv = new DataView(l.buffer);
      lv.setUint32(0, 0x04034b50, true);
      lv.setUint16(4, 20, true);
      lv.setUint16(6, 0x800, true);
      lv.setUint32(14, check, true);
      lv.setUint32(18, b.length, true);
      lv.setUint32(22, b.length, true);
      lv.setUint16(26, n.length, true);
      locals.push(l, n, b);
      const c = new Uint8Array(46),
        cv = new DataView(c.buffer);
      cv.setUint32(0, 0x02014b50, true);
      cv.setUint16(4, 20, true);
      cv.setUint16(6, 20, true);
      cv.setUint16(8, 0x800, true);
      cv.setUint32(16, check, true);
      cv.setUint32(20, b.length, true);
      cv.setUint32(24, b.length, true);
      cv.setUint16(28, n.length, true);
      cv.setUint32(42, offset, true);
      central.push(c, n);
      offset += l.length + n.length + b.length;
      count++;
    }
    const directory = concat(central),
      end = new Uint8Array(22),
      v = new DataView(end.buffer);
    v.setUint32(0, 0x06054b50, true);
    v.setUint16(8, count, true);
    v.setUint16(10, count, true);
    v.setUint32(12, directory.length, true);
    v.setUint32(16, offset, true);
    return concat([...locals, directory, end]);
  }
  const api = { queue, request, xml, dtmfPcap, files, zip };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.SipBatchCore = api;
})(typeof window === 'undefined' ? globalThis : window);
