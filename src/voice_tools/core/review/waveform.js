"use strict";
// One media element owns playback; the component only renders the original channels.
window.createReviewWaveform = (player, onRangeChange, onError, {focusSelection = false} = {}) => {
  const $ = id => document.getElementById(id);
  const colors = [
    {waveColor: "#5aaca5", progressColor: "#0d716d"},
    {waveColor: "#819dc9", progressColor: "#4268a3"},
  ];
  const regions = WaveSurfer.Regions.create();
  const wave = WaveSurfer.create({
    container: $("waveform"), media: player, height: 108,
    splitChannels: colors, normalize: false, barHeight: 1,
    barWidth: 2, barGap: 1, barRadius: 1,
    cursorWidth: 2, cursorColor: "#253f53", autoCenter: false,
    autoScroll: true, dragToSeek: true, hideScrollbar: false,
    plugins: [regions, WaveSurfer.Timeline.create({height: 28, secondaryLabelOpacity: 1, style: {fontSize: "11px"},
      formatTimeCallback: value => value < 60 ? `${Number(value.toFixed(1))}s` : `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, "0")}`})],
  });
  let entry = null, audition = null, annotation = null, ready = false, generation = 0, zoom = 1;
  const duration = () => entry?.record.result.duration_s || 0;
  const minRange = () => Math.min(.05, duration());
  function enable(value) {
    for (const id of ["zoomIn", "zoomOut", "fitWave", "focusWave", "waveGain"]) $(id).disabled = !value;
  }
  function updateHandles() {
    if (!audition) return;
    for (const [side, value] of [["left", audition.start], ["right", audition.end]]) {
      const handle = audition.element.querySelector(`[part~="region-handle-${side}"]`);
      handle.tabIndex = 0; handle.setAttribute("role", "slider");
      handle.setAttribute("aria-label", side === "left" ? "拖动试听起点" : "拖动试听终点");
      handle.setAttribute("aria-valuemin", String(side === "left" ? 0 : audition.start + minRange()));
      handle.setAttribute("aria-valuemax", String(side === "left" ? audition.end - minRange() : duration()));
      handle.setAttribute("aria-valuenow", String(value));
      handle.setAttribute("aria-valuetext", `${value.toFixed(2)} 秒`);
      handle.onkeydown = event => {
        if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
        event.preventDefault(); event.stopPropagation();
        const delta = (event.key === "ArrowLeft" ? -1 : 1) * (event.shiftKey ? 1 : .1);
        const start = side === "left" ? Math.max(0, Math.min(audition.end - minRange(), audition.start + delta)) : audition.start;
        const end = side === "right" ? Math.min(duration(), Math.max(audition.start + minRange(), audition.end + delta)) : audition.end;
        audition.setOptions({start, end}); updateHandles(); onRangeChange(start, end);
      };
    }
  }
  function syncRange(start, end) {
    if (!ready || !audition || !Number.isFinite(start + end) || start < 0 || end <= start || end > duration()) return;
    audition.setOptions({start, end}); updateHandles();
  }
  regions.on("region-update", region => {
    if (region !== audition) return;
    updateHandles(); onRangeChange(region.start, region.end);
  });
  wave.on("error", error => { enable(false); onError(`波形无法加载：${error.message}`); });
  function setZoom(value, at = player.currentTime) {
    if (!ready) return;
    zoom = Math.max(1, Math.min(16, value));
    const width = $("waveform").clientWidth;
    wave.zoom(zoom === 1 ? 0 : width / duration() * zoom);
    $("zoomValue").textContent = zoom === 1 ? "全长" : `×${Number(zoom.toFixed(1))}`;
    $("zoomOut").disabled = zoom === 1; $("zoomIn").disabled = zoom === 16;
    wave.setScrollTime(Math.max(0, at - duration() / zoom * .2));
  }
  $("zoomIn").onclick = () => setZoom(zoom * 2);
  $("zoomOut").onclick = () => setZoom(zoom / 2);
  $("fitWave").onclick = () => setZoom(1, 0);
  $("focusWave").onclick = () => {
    if (!entry) return;
    const start = focusSelection ? audition.start : Math.max(0, entry.op.at_s - 2);
    const end = focusSelection ? audition.end : Math.min(duration(), entry.op.observed_until_s + 1);
    setZoom(duration() / Math.max(.1, end - start), start);
    wave.setScrollTime(start);
  };
  $("waveGain").onchange = () => {
    const gain = Number($("waveGain").value);
    wave.setOptions({barHeight: gain});
    $("waveScale").textContent = gain === 1 ? "原始幅度 · 同一时间轴" : `显示增益 ×${gain} · 两轨同比例 · 不改变音量`;
  };
  new ResizeObserver(() => { if (ready) setZoom(zoom); }).observe($("waveform"));
  return {
    async load(value) {
      entry = value; ready = false; audition = null; annotation = null; enable(false);
      const ticket = ++generation, peaks = entry.record.waveform?.channels;
      regions.clearRegions(); $("waveform").hidden = !peaks?.length; $("waveEmpty").hidden = !!peaks?.length;
      $("waveStage").querySelector(".track-labels").hidden = !peaks?.length;
      $("waveStage").style.gridTemplateColumns = peaks?.length ? "" : "1fr";
      if (!peaks?.length) { $("waveHeading").textContent = "录音波形"; $("waveResolution").textContent = "无波形摘要"; return; }
      $("waveStage").style.setProperty("--track-count", peaks.length);
      $("rightTitle").hidden = peaks.length < 2;
      $("waveHeading").textContent = peaks.length === 1 ? "单轨波形" : "双轨波形";
      $("waveform").setAttribute("aria-label", peaks.length === 1 ? "单轨录音波形" : "同步双轨录音波形");
      $("waveResolution").textContent = `波形摘要 · 每格约 ${Math.max(1, Math.round(entry.record.waveform.bin_s * 1000))} ms`;
      wave.setOptions({minPxPerSec: 0, splitChannels: colors.slice(0, peaks.length)});
      try {
        // Precomputed min/max pairs are rendered without decoding or fetching the recording.
        await wave.load(player.getAttribute("src") ? player.src : "", peaks.map(channel => channel.flat()), duration());
        if (ticket !== generation) return;
        annotation = regions.addRegion({id: "annotation-window", start: entry.op.at_s, end: entry.op.observed_until_s,
          drag: false, resize: false, color: "rgba(232, 174, 53, .10)"});
        annotation.element.style.pointerEvents = "none";
        annotation.element.style.borderInline = "1px dashed #bd8617";
        audition = regions.addRegion({id: "audition-range", start: Number($("start").value), end: Number($("end").value),
          drag: false, resize: true, minLength: minRange(), color: "rgba(24, 156, 151, .07)"});
        audition.element.style.pointerEvents = "none";
        audition.element.style.borderInline = "1px solid #098a8a";
        ready = true; enable(true); updateHandles(); setZoom(1, 0);
        wave.setTime(Number($("start").value));
      } catch (error) { if (ticket === generation) onError(`波形无法加载：${error.message}`); }
    },
    syncRange,
    setEvidenceRange(start, end) {
      if (!entry || !Number.isFinite(start + end) || start < 0 || end <= start || end > duration()) return;
      entry = {...entry, op: {...entry.op, at_s: start, observed_until_s: end}};
      if (annotation) annotation.setOptions({start, end});
    },
    setTime(value) { if (!Number.isFinite(value)) return; if (ready) wave.setTime(value); else if (player.getAttribute("src")) player.currentTime = value; },
    syncChannel(channel) {
      $("leftTitle").classList.toggle("is-muted", channel === "right");
      $("rightTitle").classList.toggle("is-muted", channel === "left");
    },
  };
};
