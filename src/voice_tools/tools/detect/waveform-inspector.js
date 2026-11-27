"use strict";

(() => {
  // Peak pairs are signed PCM extrema on a common scale across both channels.
  function paintChannel(ctx, peaks, wave, start, span, width, centre, gain, color) {
    const first = (start - (wave.start_s || 0)) / wave.bin_s;
    const step = span / (width * wave.bin_s);
    ctx.strokeStyle = color;
    ctx.beginPath();
    if (wave.samples && step < 1) {
      for (let i = Math.max(0, Math.floor(first)); i < Math.min(peaks.length, first + width * step + 1); i++) {
        ctx.lineTo((i - first) / step, centre - peaks[i][0] * gain);
      }
    } else {
      for (let x = 0; x < width; x++) {
        const from = Math.max(0, Math.floor(first + x * step));
        const to = Math.min(peaks.length, Math.ceil(first + (x + 1) * step));
        if (from >= to) continue;
        let low = peaks[from][0], high = peaks[from][1];
        for (let i = from + 1; i < to; i++) {
          low = Math.min(low, peaks[i][0]);
          high = Math.max(high, peaks[i][1]);
        }
        ctx.moveTo(x + .5, centre - high * gain);
        ctx.lineTo(x + .5, centre - low * gain + .6);
      }
    }
    ctx.stroke();
  }

  window.createWaveformInspector = (readDetail, redraw) => {
    const status = document.getElementById("waveDetailStatus");
    let source, overview, peak, detail, key, timer, controller;
    function resetRequest() {
      clearTimeout(timer);
      controller?.abort();
      key = detail = null;
      status.textContent = "";
    }
    function updateWindow(start, end, width) {
      const bins = Math.min(4096, Math.max(64, Math.ceil(width)));
      if (overview.bin_s <= (end - start) / bins || end - start >= overview.duration_s - .000001) {
        if (key) resetRequest();
        return;
      }
      const requested = [start, end, bins].join("/");
      if (key === requested) return;
      resetRequest();
      key = requested;
      const request = new AbortController();
      controller = request;
      status.textContent = "正在读取局部波形…";
      timer = setTimeout(async () => {
        try {
          const result = await readDetail(source, start, end, bins, request.signal);
          if (request.signal.aborted) return;
          detail = result;
          status.textContent = "";
          redraw();
        } catch (error) {
          if (!request.signal.aborted) status.textContent = "局部波形读取失败：" + error.message;
        }
      }, 45);
    }
    return {
      load(recording, summary) {
        resetRequest();
        source = recording;
        overview = summary;
        peak = .02;
        for (const channel of summary?.channels || []) {
          for (const [low, high] of channel) peak = Math.max(peak, -low, high);
        }
      },
      paint(ctx, width, height, style, start, end) {
        updateWindow(start, end, width);
        const wave = detail || overview;
        const laneHeight = height / wave.channels.length;
        wave.channels.forEach((channel, index) => {
          const centre = (index + .5) * laneHeight;
          ctx.fillStyle = style.getPropertyValue("--line");
          ctx.fillRect(0, centre, width, 1);
          const color = style.getPropertyValue(index ? "--duplex-right" : "--duplex-left");
          paintChannel(ctx, channel, wave, start, end - start, width, centre, (laneHeight / 2 - 2) / peak, color);
        });
      },
    };
  };

  window.bindWaveformSelection = (plot, player, getView, seek, zoomTo) => {
    const band = document.getElementById("waveSelection");
    let drag = null;
    const fraction = event => {
      const bounds = plot.getBoundingClientRect();
      return Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width));
    };
    const cancel = () => {
      drag = null;
      band.hidden = true;
    };
    plot.addEventListener("pointerdown", event => {
      if (!event.isPrimary || event.button !== 0 || !plot.classList.contains("can-seek")) return;
      drag = {id: event.pointerId, from: fraction(event), view: getView()};
      plot.setPointerCapture(event.pointerId);
    });
    plot.addEventListener("pointermove", event => {
      if (drag?.id !== event.pointerId) return;
      const to = fraction(event), distance = Math.abs(to - drag.from);
      if (distance * plot.clientWidth < 5) { band.hidden = true; return; }
      player.pause();
      band.hidden = false;
      band.style.left = Math.min(drag.from, to) * 100 + "%";
      band.style.width = distance * 100 + "%";
    });
    plot.addEventListener("pointerup", event => {
      if (drag?.id !== event.pointerId) return;
      const {from, view: [start, end]} = drag;
      const to = fraction(event);
      cancel();
      const time = f => start + f * (end - start);
      if (Math.abs(from - to) * plot.clientWidth < 5) seek(time(to));
      else zoomTo(time(Math.min(from, to)), time(Math.max(from, to)));
    });
    plot.addEventListener("lostpointercapture", cancel);
    plot.addEventListener("pointercancel", cancel);
    return cancel;
  };
})();
