"use strict";
// Adapted from Pipecat UI's duplex-timeline-renderer.ts (BSD-2-Clause).
// See THIRD_PARTY_NOTICES.md for the pinned source and scope of the adaptation.
(() => {
  const BAR_WIDTH = 3;
  const BAR_GAP = 2;
  const AUDIBLE_LEVEL = 0.02;
  const LANE_HEIGHT = 40;
  const TICK_FRACTIONS = [0, .25, .5, .75, 1];

  // Each bar covers the corresponding time bucket in the saved peak summary.
  function bucketPeak(channel, start, end, binSeconds) {
    let peak = 0;
    const last = Math.min(channel.length, Math.ceil(end / binSeconds));
    for (let i = Math.floor(start / binSeconds); i < last; i++) {
      const [low, high] = channel[i];
      peak = Math.max(peak, Math.abs(low), Math.abs(high));
    }
    return Math.min(peak, 1);
  }

  // Pipecat's mirrored bars and lane-local alpha mask, on the recording clock.
  function drawLane(ctx, channel, {width, top, duration, binSeconds, color, gridColor}) {
    const centre = top + LANE_HEIGHT / 2;
    ctx.fillStyle = gridColor;
    ctx.fillRect(0, centre, width, 1);
    ctx.fillStyle = color;
    const slot = BAR_WIDTH + BAR_GAP;
    for (let x = 0; x < width; x += slot) {
      const peak = bucketPeak(channel, x / width * duration, (x + slot) / width * duration, binSeconds);
      // Use the renderer's dB display scale so quiet audio remains visible.
      const level = Math.min(1, (20 * Math.log10(peak + 1e-8) + 55) / 45);
      if (level <= AUDIBLE_LEVEL) continue;
      const half = Math.max(1, Math.round(level * (LANE_HEIGHT / 2 - 1)));
      ctx.fillRect(x, centre - half, BAR_WIDTH, half * 2 + 1);
    }

    const mask = ctx.createLinearGradient(0, top, 0, top + LANE_HEIGHT);
    mask.addColorStop(0, "rgba(0, 0, 0, 0.5)");
    mask.addColorStop(0.5, "rgba(0, 0, 0, 1)");
    mask.addColorStop(1, "rgba(0, 0, 0, 0.5)");
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, top, width, LANE_HEIGHT);
    ctx.clip();
    ctx.globalCompositeOperation = "destination-in";
    ctx.fillStyle = mask;
    ctx.fillRect(0, top, width, LANE_HEIGHT);
    ctx.restore();
  }

  window.createRecordingDuplexTimeline = (player, seek) => {
    const $ = (id) => document.getElementById(id);
    const timeline = $("duplexTimeline");
    const plot = $("duplexPlot");
    const canvas = $("duplexCanvas");
    const cursor = $("duplexPlayhead");
    const range = $("duplexRange");
    const ctx = canvas.getContext("2d");
    const theme = matchMedia("(prefers-color-scheme: dark)");
    let summary = null, duration = 0;

    function draw() {
      const {clientWidth: width, clientHeight: height} = canvas;
      if (!summary || !width || !height) return;
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      const style = getComputedStyle(canvas);
      const gridColor = style.getPropertyValue("--line").trim();
      const laneGap = 8;
      const padding = 6;
      summary.channels.forEach((channel, index) => {
        const color = style.getPropertyValue(index === 0 ? "--duplex-left" : "--duplex-right").trim();
        drawLane(ctx, channel, {width, top: padding + index * (LANE_HEIGHT + laneGap),
          duration, binSeconds: summary.bin_s, color, gridColor});
      });
      ctx.fillStyle = gridColor;
      for (const fraction of TICK_FRACTIONS.slice(1, -1)) {
        ctx.fillRect(Math.round(width * fraction), 0, 1, height);
      }
    }

    function syncPlayhead() {
      const available = summary && player.readyState >= 1 && !player.error;
      cursor.hidden = !available;
      plot.classList.toggle("can-seek", Boolean(available));
      if (available) cursor.style.left = Math.min(100, player.currentTime / duration * 100) + "%";
    }

    new ResizeObserver(draw).observe(canvas);
    theme.addEventListener("change", draw);
    for (const event of ["loadedmetadata", "emptied", "error", "seeking", "timeupdate"]) {
      player.addEventListener(event, syncPlayhead);
    }
    plot.addEventListener("click", (event) => {
      if (!plot.classList.contains("can-seek")) return;
      const {left, width} = canvas.getBoundingClientRect();
      const fraction = Math.max(0, Math.min(1, (event.clientX - left) / width));
      seek(fraction * duration);
      syncPlayhead();
    });

    return {
      load(waveform, seconds) {
        duration = seconds;
        $("annotationTime").replaceChildren(...TICK_FRACTIONS.map((fraction) => {
          const tick = document.createElement("span");
          tick.style.left = fraction * 100 + "%";
          tick.textContent = Number((duration * fraction).toFixed(2)) + " 秒";
          return tick;
        }));
        const channels = waveform?.channels;
        const available = Boolean(ctx && Number.isFinite(seconds) && seconds > 0 &&
          Number.isFinite(waveform?.bin_s) && waveform.bin_s > 0 &&
          channels?.length >= 1 && channels.length <= 2 && channels.every(channel => channel.length));
        summary = available ? waveform : null;
        timeline.hidden = !available;
        $("duplexRight").hidden = channels?.length !== 2;
        timeline.classList.toggle("two-channels", channels?.length === 2);
        syncPlayhead();
        draw();
        return available;
      },
      setRange(start, end) {
        if (!summary) return;
        range.style.left = start / duration * 100 + "%";
        range.style.width = (end - start) / duration * 100 + "%";
      },
    };
  };
})();
