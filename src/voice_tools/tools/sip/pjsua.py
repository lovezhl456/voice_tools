"""Optional native adapter. Only imported inside the isolated SIP worker."""
import gc
import os

import pjsua2 as pj

from .runner import CallFailure


class Call(pj.Call):
    def __init__(self, account, owner):
        super().__init__(account)
        self.owner = owner

    def onCallState(self, prm):
        try:
            info = self.getInfo()
            owner = self.owner
            owner.call_id = info.callIdString
            owner.last_code, owner.last_reason = info.lastStatusCode, info.lastReason
            owner.emit("call_state", state=info.stateText, sip_code=info.lastStatusCode)
            owner.connected = info.state == pj.PJSIP_INV_STATE_CONFIRMED
            owner.disconnected = info.state == pj.PJSIP_INV_STATE_DISCONNECTED
            if owner.connected:
                owner.attach_media()
        except Exception as exc:
            self.owner.failure = str(exc)

    def onCallMediaState(self, prm):
        try:
            self.owner.attach_media()
        except Exception as exc:
            self.owner.failure = str(exc)

    def onDtmfDigit(self, prm):
        self.owner.emit("dtmf_received", digit=prm.digit)


class Player(pj.AudioMediaPlayer):
    def __init__(self):
        super().__init__()
        self.done = False

    def onEof2(self):
        # This callback may be a media thread. No SIP operations or destruction here.
        self.done = True


class Account(pj.Account):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def onRegState(self, prm):
        self.owner.registration_code = prm.code
        self.owner.emit("registration", sip_code=prm.code)

    def onIncomingCall(self, prm):
        call = pj.Call(self, prm.callId)
        operation = pj.CallOpParam()
        operation.statusCode = 486
        call.answer(operation)
        self.owner.rejected_calls.append(call)


class Backend:
    def __init__(self, plan, output, emit, clock):
        self.plan, self.output, self.emit, self.clock = plan, output, emit, clock
        self.ep = self.account = self.call = self.audio = self.recorder = self.player = None
        self.connected = self.disconnected = self.media_ready = False
        self.failure = None
        self.record_started = None
        self.registration_code = 0
        self.call_id, self.last_reason, self.last_code = None, None, None
        self.rejected_calls = []
        self.codec = None
        self.closed = False
        try:
            self._initialize()
        except BaseException:
            try:
                self.close()
            except Exception:
                pass  # Cleanup already attempted every resource; preserve the initialization error.
            raise

    def _initialize(self):
        plan = self.plan
        self.ep = pj.Endpoint()
        self.ep.libCreate()
        config = pj.EpConfig()
        config.uaConfig.threadCnt = 0
        config.uaConfig.mainThreadOnly = True
        config.uaConfig.maxCalls = 1
        config.logConfig.level = 0
        config.logConfig.consoleLevel = 0
        config.medConfig.clockRate = 8000
        config.medConfig.sndClockRate = 8000
        config.medConfig.channelCount = 1
        self.ep.libInit(config)
        transport = pj.TransportConfig()
        net = plan["network"]
        transport.port = net["sip_port"]
        transport.boundAddress = net.get("bind_address", "")
        transport.publicAddress = net.get("public_address", "")
        self.ep.transportCreate(pj.PJSIP_TRANSPORT_UDP, transport)
        self.ep.libStart()
        self.ep.audDevManager().setNullDev()
        # Force the tested narrowband codec; telephone-event is negotiated separately.
        for codec in self.ep.codecEnum2():
            self.ep.codecSetPriority(codec.codecId, 255 if codec.codecId.startswith(plan["codec"] + "/8000") else 0)
        cfg = pj.AccountConfig()
        cfg.idUri = plan["account"]["id_uri"]
        cfg.regConfig.registrarUri = plan["account"].get("registrar_uri", "")
        cfg.regConfig.registerOnAdd = bool(cfg.regConfig.registrarUri)
        cfg.mediaConfig.transportConfig.port = net["rtp_port"]
        cfg.mediaConfig.transportConfig.portRange = 2
        cfg.mediaConfig.transportConfig.boundAddress = net.get("bind_address", "")
        cfg.mediaConfig.transportConfig.publicAddress = net.get("public_address", "")
        if plan["account"].get("proxy_uri"):
            cfg.sipConfig.proxies.append(plan["account"]["proxy_uri"])
        auth = plan["account"].get("auth")
        if auth:
            secret = os.environ.get(auth["password_env"])
            if not secret:
                raise ValueError("认证环境变量缺失：" + auth["password_env"])
            cfg.sipConfig.authCreds.append(pj.AuthCredInfo("digest", auth.get("realm", "*"), auth["username"], 0, secret))
        self.account = Account(self)
        self.account.create(cfg)
        if cfg.regConfig.registerOnAdd:
            deadline = self.clock() + plan["connect_timeout_s"]
            while self.registration_code < 200 and self.clock() < deadline:
                self.poll(20)
            if not 200 <= self.registration_code < 300:
                raise CallFailure("REGISTRATION_FAILED", f"注册失败／超时，SIP {self.registration_code}")

    def dial(self):
        self.call = Call(self.account, self)
        params = pj.CallOpParam(True)
        params.opt.audioCount, params.opt.videoCount = 1, 0
        target = self.plan["target_uri"]
        if ";transport=" not in target:
            target += ";transport=udp"
        self.call.makeCall(target, params)

    def attach_media(self):
        if not self.call or self.disconnected:
            return
        info = self.call.getInfo()
        previous = self.audio
        self.media_ready = False
        for item in info.media:
            if item.type != pj.PJMEDIA_TYPE_AUDIO or item.status != pj.PJSUA_CALL_MEDIA_ACTIVE:
                continue
            audio = self.call.getAudioMedia(item.index)
            if previous and previous.getPortId() != audio.getPortId():
                self.detach_media(previous)
            self.audio = audio
            try:
                self.codec = self.call.getStreamInfo(item.index).codecName
            except pj.Error:
                pass
            if self.recorder is None and (self.connected or self.plan["record_early"]):
                self.recorder = pj.AudioMediaRecorder()
                self.recorder.createRecorder(str(self.output / "rx.wav"))
                self.record_started = self.clock()
                self.emit("recording_start", file="rx.wav", role="remote_received", early=not self.connected)
            # SDP updates can replace the conference port after 183 or re-INVITE.
            # Reconnecting an existing edge is idempotent; the WAV stays open.
            if self.recorder is not None:
                self.audio.startTransmit(self.recorder)
            if self.player is not None:
                self.player.startTransmit(self.audio)
            self.media_ready = True
            return
        if previous:
            self.detach_media(previous)
        self.audio = None

    def detach_media(self, audio):
        if self.recorder is not None:
            try:
                audio.stopTransmit(self.recorder)
            except pj.Error:
                pass  # PJSIP may already have removed the previous port.
        if self.player is not None:
            try:
                self.player.stopTransmit(audio)
            except pj.Error:
                pass

    def poll(self, milliseconds):
        self.ep.libHandleEvents(milliseconds)

    def play(self, filename):
        self.stop_playback()
        self.player = Player()
        self.player.createPlayer(filename, pj.PJMEDIA_FILE_NO_LOOP)
        self.player.startTransmit(self.audio)

    def playback_done(self):
        return self.player is not None and self.player.done

    def stop_playback(self):
        if self.player:
            if self.audio:
                try:
                    self.player.stopTransmit(self.audio)
                except pj.Error:
                    pass
            self.player = None
            gc.collect()

    def dtmf(self, digit, duration, method):
        prm = pj.CallSendDtmfParam()
        prm.method = pj.PJSUA_DTMF_METHOD_RFC2833 if method == "rfc4733" else pj.PJSUA_DTMF_METHOD_SIP_INFO
        prm.duration, prm.digits = duration, digit
        self.call.sendDtmf(prm)

    def hangup(self):
        if self.call and not self.disconnected:
            self.call.hangup(pj.CallOpParam())

    def details(self):
        return {"call_id": self.call_id, "last_sip_code": self.last_code, "last_reason": self.last_reason,
                "codec_negotiated": self.codec, "connected": self.connected}

    def close(self):
        if self.closed:
            return
        self.closed = True
        errors = []

        def cleanup(action):
            try:
                action()
            except Exception as exc:
                errors.append(exc)

        if self.ep:
            def end_call():
                self.hangup()
                for _ in range(50):
                    if not self.call or self.disconnected:
                        break
                    self.poll(20)
            cleanup(end_call)
        cleanup(self.stop_playback)
        if self.audio:
            cleanup(lambda: self.detach_media(self.audio))
        self.player = self.audio = self.recorder = None
        self.call = None
        self.rejected_calls.clear()
        if self.account:
            cleanup(self.account.shutdown)
        self.account = None
        gc.collect()
        if self.ep:
            cleanup(self.ep.libDestroy)
        self.ep = None
        gc.collect()
        if errors:
            raise errors[0]
