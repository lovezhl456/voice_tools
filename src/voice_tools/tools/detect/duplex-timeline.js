"use strict";
// Adapted from Pipecat UI's duplex-timeline-renderer.ts (BSD-2-Clause).
// See THIRD_PARTY_NOTICES.md for the pinned source and scope of the adaptation.
(() => {
  const BAR_WIDTH = 2;
  const BAR_GAP = 1;
  const AUDIBLE_LEVEL = 0.02;
  const LANE_HEIGHT = 52;
  const MAX_ZOOM = 8;
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
  function drawLane(ctx, channel, {width, top, start, span, binSeconds, color}) {
    const centre = top + LANE_HEIGHT / 2;
    ctx.fillStyle = color;
    const slot = BAR_WIDTH + BAR_GAP;
    for (let x = 0; x < width; x += slot) {
      const peak = bucketPeak(channel, start + x / width * span, start + (x + slot) / width * span, binSeconds);
      // Use the renderer's dB display scale so quiet audio remains visible.
      const level = Math.min(1, (20 * Math.log10(peak + 1e-8) + 55) / 45);
      if (level <= AUDIBLE_LEVEL) continue;
      const half = Math.max(1, Math.round(level * (LANE_HEIGHT / 2 - 1)));
      ctx.fillRect(x, centre - half, BAR_WIDTH, half * 2 + 1);
    }

    const mask = ctx.createLinearGradient(0, top, 0, top + LANE_HEIGHT);
    mask.addColorStop(0, "rgba(0, 0, 0, 0.75)");
    mask.addColorStop(0.5, "rgba(0, 0, 0, 1)");
    mask.addColorStop(1, "rgba(0, 0, 0, 0.75)");
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, top, width, LANE_HEIGHT);
    ctx.clip();
    ctx.globalCompositeOperation = "destination-in";
    ctx.fillStyle = mask;
    ctx.fillRect(0, top, width, LANE_HEIGHT);
    ctx.restore();

    ctx.save();
    ctx.strokeStyle = color;
    ctx.globalAlpha = 0.6;
    ctx.setLineDash([2, 2]);
    ctx.beginPath();
    ctx.moveTo(0, centre + .5);
    ctx.lineTo(width, centre + .5);
    ctx.stroke();
    ctx.restore();
  }

  window.createRecordingDuplexTimeline = (player, seek, onView, readDetail) => {
    const $ = (id) => document.getElementById(id);
    const timeline = $("duplexTimeline");
    const plot = $("duplexWave");
    const canvas = $("duplexCanvas");
    const cursor = $("duplexPlayhead");
    const range = $("duplexRange");
    const evidence = $("duplexEvidence");
    const markerList = $("annotationTimeline");
    const ctx = canvas.getContext("2d");
    const theme = matchMedia("(prefers-color-scheme: dark)");
    let summary = null, duration = 0, plotWidth = 0;
    let zoom = 1, viewStart = 0, auditionRange = null, evidenceRange = null;
    const inspector = readDetail && window.createWaveformInspector(readDetail, draw);
    const maxZoom = () => inspector ? Math.max(1, duration / .02) : MAX_ZOOM;
    const viewSpan = () => duration / zoom;
    const viewEnd = () => viewStart + viewSpan();
    const percent = (time) => (time - viewStart) / viewSpan() * 100 + "%";

    function draw() {
      const {clientWidth: width, clientHeight: height} = canvas;
      if (!summary || !width || !height) return;
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      const style = getComputedStyle(canvas);
      if (inspector) {
        inspector.paint(ctx, width, height, style, viewStart, viewEnd());
        return;
      }
      const gridColor = style.getPropertyValue("--line").trim();
      summary.channels.forEach((channel, index) => {
        const color = style.getPropertyValue(index === 0 ? "--duplex-left" : "--duplex-right").trim();
        drawLane(ctx, channel, {width, top: index * LANE_HEIGHT, start: viewStart,
          span: viewSpan(), binSeconds: summary.bin_s, color});
      });
      ctx.fillStyle = gridColor;
      if (summary.channels.length === 2) ctx.fillRect(0, LANE_HEIGHT, width, 1);
    }

    function clip(bounds) {
      if (!bounds) return null;
      const start = Math.max(bounds.start, viewStart);
      const end = Math.min(bounds.end, viewEnd());
      return start < end ? {start, end} : null;
    }

    function layoutRange(element, bounds) {
      const visible = clip(bounds);
      element.hidden = !visible;
      if (!visible) return;
      element.style.left = percent(visible.start);
      element.style.width = (visible.end - visible.start) / viewSpan() * 100 + "%";
      if (element === evidence) {
        element.style.borderInlineStartWidth = bounds.start < viewStart ? "0" : "";
        element.style.borderInlineEndWidth = bounds.end > viewEnd() ? "0" : "";
      }
    }

    function layoutAnnotations() {
      const laneEnds = [];
      const visibleLabels = new Set();
      for (const mark of markerList.children) {
        const bounds = clip({start: Number(mark.dataset.start), end: Number(mark.dataset.end)});
        mark.hidden = !bounds;
        if (!bounds) continue;
        const label = mark.textContent;
        mark.toggleAttribute("data-repeat", visibleLabels.has(label));
        visibleLabels.add(label);
        const freeLane = laneEnds.findIndex(end => end <= bounds.start);
        const lane = freeLane < 0 ? laneEnds.length : freeLane;
        laneEnds[lane] = bounds.end;
        mark.style.setProperty("--lane", String(lane));
        mark.style.setProperty("--start", percent(bounds.start));
        mark.style.setProperty("--width", (bounds.end - bounds.start) / viewSpan() * 100 + "%");
      }
      markerList.style.setProperty("--lane-count", String(laneEnds.length));
      markerList.hidden = !laneEnds.length;
    }

    function setView(nextZoom, at) {
      zoom = inspector ? Math.min(maxZoom(), Math.max(1, nextZoom)) : nextZoom;
      viewStart = Math.max(0, Math.min(duration - viewSpan(), at - viewSpan() / 2));
      $("duplexZoomOut").disabled = !summary || zoom === 1;
      $("duplexZoomIn").disabled = !summary || zoom >= maxZoom();
      $("duplexFit").disabled = !summary;
      $("duplexFit").textContent = inspector || zoom === 1 ? "全长" : "×" + zoom;
      if (inspector) {
        $("wavePan").hidden = zoom === 1;
        $("wavePrev").disabled = viewStart <= .000001;
        $("waveNext").disabled = viewEnd() >= duration - .000001;
      }
      const digits = viewSpan() < 4 ? 2 : 1;
      $("annotationTime").replaceChildren(...TICK_FRACTIONS.map((fraction) => {
        const tick = document.createElement("span");
        tick.style.left = fraction * 100 + "%";
        tick.textContent = (viewStart + viewSpan() * fraction).toFixed(digits) + " s";
        return tick;
      }));
      layoutAnnotations();
      layoutRange(range, auditionRange);
      layoutRange(evidence, evidenceRange);
      draw();
      if (onView) onView(viewStart, viewEnd());
    }

    function setPosition(time) {
      if (!summary || !plotWidth || cursor.hidden) return;
      const at = Math.min(duration, time);
      // Media seeks round to the browser timebase; a rounded boundary still belongs to this view.
      if (at < viewStart - 0.001 || at > viewEnd() + 0.001) setView(zoom, at);
      cursor.style.translate = ((at - viewStart) / viewSpan() * plotWidth - 1) + "px 0";
    }

    function syncPlayhead() {
      const available = Boolean(summary && player.readyState >= 1 && !player.error);
      cursor.hidden = !available;
      plot.classList.toggle("can-seek", available);
      if (available) setPosition(player.currentTime);
    }

    const zoomAt = () => player.readyState >= 1 ? player.currentTime : evidenceRange?.start ?? 0;
    function changeZoom(value) {
      setView(value, zoomAt());
      syncPlayhead();
    }
    $("duplexZoomOut").onclick = () => changeZoom(Math.max(1, zoom / 2));
    $("duplexZoomIn").onclick = () => changeZoom(Math.min(maxZoom(), zoom * 2));
    $("duplexFit").onclick = () => changeZoom(1);
    new ResizeObserver(([entry]) => {
      plotWidth = entry.contentRect.width;
      draw();
      syncPlayhead();
    }).observe(canvas);
    theme.addEventListener("change", draw);
    for (const event of ["loadedmetadata", "emptied", "error", "seeking"]) {
      player.addEventListener(event, syncPlayhead);
    }
    let cancelSelection;
    if (inspector) {
      const expand = $("waveExpand");
      const setExpanded = value => {
        timeline.classList.toggle("wave-expanded", value);
        expand.setAttribute("aria-pressed", value);
        expand.textContent = value ? "收起大图" : "放大查看";
      };
      const inspectWindow = (left, right) => {
        player.pause();
        seek(left);
        setView(duration / (right - left), (left + right) / 2);
        syncPlayhead();
      };
      expand.onclick = () => {
        if (!summary) return;
        const expanding = !timeline.classList.contains("wave-expanded");
        setExpanded(expanding);
        if (expanding && viewSpan() > 8) changeZoom(duration / 8);
      };
      const pan = direction => {
        const span = viewSpan();
        const start = Math.max(0, Math.min(duration - span, viewStart + direction * span));
        inspectWindow(start, start + span);
      };
      $("wavePrev").onclick = () => pan(-1);
      $("waveNext").onclick = () => pan(1);
      cancelSelection = window.bindWaveformSelection(plot, player, () => [viewStart, viewEnd()], seek, (left, right) => {
        setExpanded(true);
        inspectWindow(left, right);
      });
    } else {
      plot.addEventListener("click", (event) => {
        if (!plot.classList.contains("can-seek")) return;
        const {left, width} = canvas.getBoundingClientRect();
        const fraction = Math.max(0, Math.min(1, (event.clientX - left) / width));
        seek(viewStart + fraction * viewSpan());
        syncPlayhead();
      });

    }

    return {
      load(waveform, seconds, source) {
        cancelSelection?.();
        duration = seconds;
        auditionRange = null;
        evidenceRange = null;
        const channels = waveform?.channels;
        const available = Boolean(ctx && Number.isFinite(seconds) && seconds > 0 &&
          Number.isFinite(waveform?.bin_s) && waveform.bin_s > 0 &&
          channels?.length >= 1 && channels.length <= 2 && channels.every(channel => channel.length));
        summary = available ? waveform : null;
        inspector?.load(source, summary);
        timeline.hidden = false;
        plot.hidden = !available;
        $("duplexChannels").hidden = !available;
        $("duplexRight").hidden = channels?.length !== 2;
        timeline.classList.toggle("two-channels", channels?.length === 2);
        setView(1, 0);
        syncPlayhead();
        return available;
      },
      layoutAnnotations,
      setPosition,
      showWindow(start, end) {
        if (summary && end > start) setView(Math.max(1, duration / (end - start)), (start + end) / 2);
      },
      setRange(start, end) {
        auditionRange = {start, end};
        layoutRange(range, auditionRange);
      },
      setEvidence(start, end, visible) {
        evidenceRange = visible ? {start, end} : null;
        if (visible && (start < viewStart - 0.001 || start > viewEnd() + 0.001)) setView(zoom, start);
        else layoutRange(evidence, evidenceRange);
      },
    };
  };
})();
