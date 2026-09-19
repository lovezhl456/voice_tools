"use strict";
// Shared by the review and reference pages. The media element remains the only player.
window.createReviewPlayback = (player, {duration, seek, rangeChanged, error}) => {
  const $ = id => document.getElementById(id);
  let frame = 0, wantsToPlay = false, pending = null, sourceVersion = 0;
  function bounds() {
    const start = $("start").valueAsNumber, end = $("end").valueAsNumber, length = duration();
    if (!Number.isFinite(start) || !Number.isFinite(end) || !Number.isFinite(length) || length <= 0 ||
        start < 0 || end > length + 1e-6 || end - start < Math.min(.05, length) - 1e-6) {
      throw Error("请填写有效试听起止：范围须在录音内，且至少 0.05 秒（短于此时长的录音须选全长）。");
    }
    return [start, Math.min(end, length)];
  }
  function showState() {
    const active = !player.paused || (wantsToPlay && pending?.resume);
    $("play").textContent = active ? "Ⅱ 暂停" : "▶ 播放";
    $("play").setAttribute("aria-label", active ? "暂停试听" : "播放试听");
  }
  function showTime() {
    const length = duration();
    if (!Number.isFinite(length) || length <= 0) return;
    $("seek").value = player.currentTime;
    $("time").textContent = `${player.currentTime.toFixed(2)} / ${length.toFixed(2)} 秒`;
  }
  function stopFrame() { cancelAnimationFrame(frame); frame = 0; }
  function pause() {
    wantsToPlay = false; if (pending) pending.resume = false;
    player.pause(); stopFrame(); showState();
  }
  function checkRange() {
    if (player.paused) return;
    try {
      const [start, end] = bounds();
      if (player.currentTime < start) player.currentTime = start;
      else if (player.currentTime >= end) {
        if ($("loop").checked) player.currentTime = start;
        else { pause(); player.currentTime = end; }
      }
    } catch (e) { pause(); error(e.message); }
  }
  function tick() {
    frame = 0; checkRange(); showTime();
    if (!player.paused) frame = requestAnimationFrame(tick);
  }
  function reportPlayError(e, version) {
    if (version !== sourceVersion || e.name === "AbortError") return;
    pause(); error(e.message);
  }
  async function toggle() {
    if ($("play").disabled) return;
    if (!player.paused || wantsToPlay) { pause(); return; }
    const version = sourceVersion;
    try {
      const [start, end] = bounds(); wantsToPlay = true;
      if (pending) { pending.resume = true; pending.at = start; showState(); return; }
      if (player.currentTime < start || player.currentTime >= end) player.currentTime = start;
      await player.play();
    } catch (e) { reportPlayError(e, version); }
  }
  function setSource(url, keepTime = true) {
    const at = keepTime ? (pending?.at ?? player.currentTime) : $("start").valueAsNumber;
    const resume = keepTime && (wantsToPlay || !player.paused), version = ++sourceVersion;
    player.pause(); stopFrame(); player.onloadedmetadata = null;
    wantsToPlay = resume; $("play").disabled = !url;
    pending = url ? {at, resume, version} : null;
    if (!url) { wantsToPlay = false; player.removeAttribute("src"); player.load(); showState(); return; }
    const expectedURL = new URL(url, location.href).href;
    player.onloadedmetadata = () => {
      if (!pending || pending.version !== version || player.currentSrc !== expectedURL) return;
      const state = pending; pending = null;
      player.currentTime = Math.max(0, Math.min(state.at, player.duration));
      showTime(); showState();
      if (state.resume) player.play().catch(e => reportPlayError(e, version));
    };
    player.src = url; player.load(); showState();
  }
  function applyRange(movePlayhead = false) {
    try {
      const [start, end] = bounds();
      $("start").removeAttribute("aria-invalid"); $("end").removeAttribute("aria-invalid");
      rangeChanged(start, end);
      if (movePlayhead) { if (pending) pending.at = start; seek(start); }
      checkRange(); error(""); return true;
    } catch (e) {
      pause(); $("start").setAttribute("aria-invalid", "true"); $("end").setAttribute("aria-invalid", "true");
      error(e.message); return false;
    }
  }
  function setRange(start, end) {
    $("start").value = Number(start.toFixed(6)); $("end").value = Number(end.toFixed(6));
    applyRange();
  }
  player.addEventListener("play", () => { wantsToPlay = true; stopFrame(); frame = requestAnimationFrame(tick); showState(); });
  player.addEventListener("pause", () => { stopFrame(); showState(); });
  player.addEventListener("timeupdate", () => { checkRange(); showTime(); });
  for (const event of ["loadedmetadata", "emptied"]) player.addEventListener(event, showState);
  player.addEventListener("error", () => { pause(); pending = null; });
  player.addEventListener("ended", () => {
    stopFrame(); showState();
    if (!wantsToPlay || !$("loop").checked) { wantsToPlay = false; return; }
    const version = sourceVersion;
    try { player.currentTime = bounds()[0]; player.play().catch(e => reportPlayError(e, version)); }
    catch (e) { pause(); error(e.message); }
  });
  $("play").onclick = toggle;
  $("seek").oninput = () => { const value = $("seek").valueAsNumber; if (Number.isFinite(value)) { seek(value); checkRange(); } };
  for (const id of ["start", "end"]) $(id).onchange = () => applyRange(true);
  return {bounds, toggle, pause, setSource, setRange, applyRange};
};
