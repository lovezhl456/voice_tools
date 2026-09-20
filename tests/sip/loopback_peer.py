"""Independent minimal localhost-only SIP/RTP peer for integration acceptance.

Not a production SIP server. It checks actual UDP packets, answers with G.711,
receives telephone-event/SIP INFO and saves an inspectable synthetic PCAP.
"""
import argparse
import hashlib
import json
import math
import re
import select
import socket
import struct
import time
from pathlib import Path

from tests.sip.fixtures import CaptureWriter, rtp


def serve(directory, mode="answer", duration=12):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    sip = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); sip.bind(("127.0.0.1", 0))
    media = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); media.bind(("127.0.0.1", 0))
    alternate = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); alternate.bind(("127.0.0.1", 0))
    current_media = media
    port, media_port = sip.getsockname()[1], media.getsockname()[1]
    capture = CaptureWriter(directory / "peer.pcap")
    report = {"sip_port": port, "rtp_port": media_port, "mode": mode, "audio_packets": 0, "non_silent_packets": 0,
              "dtmf": [], "sip_info": [], "answered": False, "bye_received": False, "auth_verified": False, "registers": 0,
              "reinvite_code": None, "post_switch_non_silent_packets": 0}
    (directory / "ready.json").write_text(json.dumps(report))
    remote_rtp = None
    remote_dtmf_pt = None
    tone_cycle = None
    if mode == "tone":
        from voice_tools.audio.rtp import decode_g711
        levels = struct.unpack('<256h', decode_g711(bytes(range(256)), 'PCMA'))
        tone_cycle = bytes(min(range(256), key=lambda code: abs(levels[code] -
            5000 * (math.sin(2 * math.pi * 440 * i / 8000) + math.sin(2 * math.pi * 480 * i / 8000))))
            for i in range(200))
    media_start = None
    next_audio = None
    seq = stamp = 0
    incoming_events = set()
    echoed_events = {}
    source_addr = None
    invite_headers = None
    deferred_answer = None
    finish = time.monotonic() + duration
    peer_bye_sent = False
    reinvite_sent = False
    interrupted_at = None

    def digest_verified(headers, method):
        auth = dict(re.findall(r'(\w+)="([^"]*)"', headers.get("authorization", "")))
        md5 = lambda s: hashlib.md5(s.encode()).hexdigest()
        expected = md5(md5("tester:local-test:fixture-secret") + ":fixed-nonce:" + md5(method + ":" + auth.get("uri", "")))
        return auth.get("response") == expected

    def transmit(data, address, sock):
        sock.sendto(data, address)
        capture.write(time.time(), "127.0.0.1", sock.getsockname()[1], address[0], address[1], data)

    def response(headers, address, code, reason, body="", extra=""):
        text = (f"SIP/2.0 {code} {reason}\r\nVia: {headers['via']}\r\nFrom: {headers['from']}\r\n"
                f"To: {headers['to'].split(';tag=')[0]};tag=local-test\r\nCall-ID: {headers['call-id']}\r\n"
                f"CSeq: {headers['cseq']}\r\n" + ("" if "Contact:" in extra else f"Contact: <sip:peer@127.0.0.1:{port}>\r\n") + extra +
                ("Content-Type: application/sdp\r\n" if body else "") + f"Content-Length: {len(body.encode())}\r\n\r\n{body}")
        transmit(text.encode(), address, sip)

    body = (f"v=0\r\no=peer 1 1 IN IP4 127.0.0.1\r\ns=local-test\r\nc=IN IP4 127.0.0.1\r\nt=0 0\r\n"
            f"m=audio {media_port} RTP/AVP 8 0 101\r\na=rtpmap:8 PCMA/8000\r\na=rtpmap:0 PCMU/8000\r\n"
            "a=rtpmap:101 telephone-event/8000\r\na=fmtp:101 0-15\r\na=sendrecv\r\n")
    try:
        while time.monotonic() < finish:
            now = time.monotonic()
            if deferred_answer and now >= deferred_answer:
                response(invite_headers, source_addr, 200, "OK", body)
                report["answered"] = True; deferred_answer = None
            if remote_rtp and next_audio is not None and now >= next_audio and mode != "no_rtp":
                # Known non-zero PCMA waveform, independent of production decoder.
                payload = b"\xd5" * 160 if mode == "silence" else b"\xaa" * 80 + b"\x2a" * 80
                if tone_cycle is not None:
                    payload = bytes(tone_cycle[(stamp + i) % len(tone_cycle)] for i in range(160))
                if mode == "timing_gap" and media_start and .6 <= now - media_start < 1.4:
                    payload = b"\xd5" * 160
                if mode in ("timing_stop", "timing_reply") and interrupted_at and now >= interrupted_at + .12:
                    reply = mode == "timing_reply" and .8 <= now - interrupted_at < 1.2
                    if not reply:
                        payload = b"\xd5" * 160
                transmit(rtp(seq, stamp, payload, 8, 4321), remote_rtp, current_media)
                seq, stamp, next_audio = seq + 1, stamp + 160, next_audio + .02
            if mode == "reinvite" and media_start and now - media_start >= .35 and not reinvite_sent:
                h = invite_headers
                changed = body.replace("o=peer 1 1", "o=peer 1 2").replace(f"m=audio {media_port}", f"m=audio {alternate.getsockname()[1]}")
                target = h["contact"].split("<", 1)[1].split(">", 1)[0]
                message = (f"INVITE {target} SIP/2.0\r\nVia: SIP/2.0/UDP 127.0.0.1:{port};branch=z9hG4bKswitch\r\n"
                           f"From: {h['to'].split(';tag=')[0]};tag=local-test\r\nTo: {h['from']}\r\nCall-ID: {h['call-id']}\r\n"
                           f"CSeq: 21 INVITE\r\nContact: <sip:peer@127.0.0.1:{port}>\r\nMax-Forwards: 70\r\n"
                           f"Content-Type: application/sdp\r\nContent-Length: {len(changed.encode())}\r\n\r\n{changed}")
                transmit(message.encode(), source_addr, sip); reinvite_sent = True
            if mode == "hangup" and report["answered"] and media_start and now - media_start >= .25 and not peer_bye_sent:
                h = invite_headers
                message = (f"BYE sip:tester@127.0.0.1 SIP/2.0\r\nVia: SIP/2.0/UDP 127.0.0.1:{port};branch=z9hG4bKpeerbye\r\n"
                           f"From: {h['to'].split(';tag=')[0]};tag=local-test\r\nTo: {h['from']}\r\nCall-ID: {h['call-id']}\r\nCSeq: 20 BYE\r\nContent-Length: 0\r\n\r\n")
                transmit(message.encode(), source_addr, sip)
                peer_bye_sent = True; finish = now + 1
            ready, _, _ = select.select([sip, media, alternate], [], [], .005)
            for sock in ready:
                data, address = sock.recvfrom(65535)
                capture.write(time.time(), address[0], address[1], "127.0.0.1", sock.getsockname()[1], data)
                if sock is media or sock is alternate:
                    if len(data) < 12 or data[0] >> 6 != 2: continue
                    pt = data[1] & 127
                    stamp_rx, ssrc = struct.unpack("!II", data[4:12])
                    payload = data[12 + 4 * (data[0] & 15):]
                    if pt == 101 and len(payload) >= 4:
                        key = (ssrc, stamp_rx, payload[0])
                        if key not in incoming_events:
                            incoming_events.add(key); report["dtmf"].append("0123456789*#ABCD"[payload[0]])
                        if mode == "echo_dtmf" and remote_rtp and remote_dtmf_pt is not None:
                            event_stamp = echoed_events.setdefault(key, stamp)
                            transmit(rtp(seq, event_stamp, payload, remote_dtmf_pt, 4321), remote_rtp, current_media)
                            seq += 1
                    elif pt in (0, 8):
                        report["audio_packets"] += 1
                        report["non_silent_packets"] += int(any(b not in (0xd5, 0x55, 0xff, 0x7f) for b in payload))
                        if report["non_silent_packets"] >= 3 and interrupted_at is None:
                            interrupted_at = now
                            report["interruption_received_s"] = now - media_start if media_start else None
                        if sock is alternate:
                            report["post_switch_non_silent_packets"] += int(any(b not in (0xd5, 0x55, 0xff, 0x7f) for b in payload))
                    continue
                text = data.decode("utf-8", "replace")
                start, *lines = text.split("\r\n")
                headers = {}
                for line in lines:
                    if not line: break
                    if ":" in line:
                        key, value = line.split(":", 1); headers[key.lower()] = value.strip()
                if start.startswith("SIP/2.0") and headers.get("cseq") == "21 INVITE":
                    report["reinvite_code"] = int(start.split()[1])
                    if report["reinvite_code"] == 200:
                        h = invite_headers
                        target = h["contact"].split("<", 1)[1].split(">", 1)[0]
                        ack = (f"ACK {target} SIP/2.0\r\nVia: SIP/2.0/UDP 127.0.0.1:{port};branch=z9hG4bKswitchack\r\n"
                               f"From: {h['to'].split(';tag=')[0]};tag=local-test\r\nTo: {h['from']}\r\nCall-ID: {h['call-id']}\r\n"
                               "CSeq: 21 ACK\r\nMax-Forwards: 70\r\nContent-Length: 0\r\n\r\n")
                        transmit(ack.encode(), source_addr, sip)
                        match = re.search(r"m=audio (\d+)", text)
                        if match: remote_rtp = ("127.0.0.1", int(match[1]))
                        current_media = alternate
                elif start.startswith("REGISTER "):
                    report["registers"] += 1
                    if mode == "register_auth":
                        if not digest_verified(headers, "REGISTER"):
                            response(headers, address, 401, "Unauthorized", extra='WWW-Authenticate: Digest realm="local-test", nonce="fixed-nonce", algorithm=MD5\r\n')
                            continue
                        report["auth_verified"] = True
                    response(headers, address, 200, "OK", extra=f"Contact: {headers.get('contact', '*')};expires=60\r\nExpires: 60\r\n")
                elif start.startswith("INVITE "):
                    if mode == "drop": continue
                    if mode == "reject":
                        response(headers, address, 486, "Busy Here"); finish = now + .4; continue
                    if mode == "auth" and not report["auth_verified"]:
                        if not digest_verified(headers, "INVITE"):
                            response(headers, address, 401, "Unauthorized", extra='WWW-Authenticate: Digest realm="local-test", nonce="fixed-nonce", algorithm=MD5\r\n')
                            continue
                        report["auth_verified"] = True
                    match = re.search(r"m=audio (\d+)", text)
                    if not match: continue
                    remote_rtp = ("127.0.0.1", int(match[1]))
                    offered_dtmf = re.search(r"a=rtpmap:(\d+) telephone-event/8000", text)
                    remote_dtmf_pt = int(offered_dtmf[1]) if offered_dtmf else None
                    report["remote_dtmf_pt"] = remote_dtmf_pt
                    source_addr, invite_headers = address, headers
                    if mode == "early":
                        response(headers, address, 183, "Session Progress", body)
                        next_audio = media_start = now; deferred_answer = now + .4
                    else:
                        response(headers, address, 200, "OK", body)
                        report["answered"] = True
                elif start.startswith("ACK ") and report["answered"] and next_audio is None:
                    next_audio = media_start = now
                elif start.startswith("INFO "):
                    match = re.search(r"Signal\s*=\s*([0-9A-D*#])", text, re.I)
                    if match: report["sip_info"].append(match[1])
                    response(headers, address, 200, "OK")
                    if mode == "echo_info" and match:
                        h = invite_headers
                        target = h["contact"].split("<", 1)[1].split(">", 1)[0]
                        serial = 30 + len(report["sip_info"])
                        content = f"Signal={match[1]}\r\nDuration=160\r\n"
                        message = (f"INFO {target} SIP/2.0\r\nVia: SIP/2.0/UDP 127.0.0.1:{port};branch=z9hG4bKinfo{serial}\r\n"
                                   f"From: {h['to'].split(';tag=')[0]};tag=local-test\r\nTo: {h['from']}\r\nCall-ID: {h['call-id']}\r\n"
                                   f"CSeq: {serial} INFO\r\nMax-Forwards: 70\r\nContent-Type: application/dtmf-relay\r\n"
                                   f"Content-Length: {len(content)}\r\n\r\n{content}")
                        transmit(message.encode(), source_addr, sip)
                elif start.startswith("BYE "):
                    report["bye_received"] = True; response(headers, address, 200, "OK"); finish = now + .1
                elif start.startswith("CANCEL "):
                    response(headers, address, 200, "OK")
                    if invite_headers: response(invite_headers, address, 487, "Request Terminated")
                    finish = now + .1
    finally:
        capture.close(); sip.close(); media.close(); alternate.close()
        (directory / "report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--mode", default="answer", choices=("answer", "auth", "early", "reject", "drop", "hangup", "register", "register_auth", "reinvite", "silence", "no_rtp", "echo_dtmf", "echo_info", "tone", "timing_stop", "timing_reply", "timing_gap"))
    parser.add_argument("--duration", type=float, default=12)
    args = parser.parse_args()
    serve(args.out, args.mode, args.duration)
