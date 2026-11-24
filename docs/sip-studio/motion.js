'use strict';

window.VoiceMotion = (() => {
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');
  const running = new Map();

  function stop(element) {
    running.get(element)?.cancel();
    running.delete(element);
  }

  reduce.addEventListener('change', () => {
    if (reduce.matches) for (const element of running.keys()) stop(element);
  });

  function animate(element, keyframes, durationName) {
    stop(element);
    if (reduce.matches || !element.animate) return;
    const style = getComputedStyle(document.documentElement);
    const animation = element.animate(keyframes, {
      duration: parseFloat(style.getPropertyValue(durationName)),
      easing: style.getPropertyValue('--motion-ease').trim(),
    });
    running.set(element, animation);
    const release = () => {
      if (running.get(element) === animation) running.delete(element);
    };
    animation.addEventListener('finish', release, {once:true});
    animation.addEventListener('cancel', release, {once:true});
  }

  function reveal(element, effect = 'view') {
    if (!element || reduce.matches) return;
    const previous = running.has(element) ? getComputedStyle(element) : null;
    const isFeedback = effect === 'feedback';
    const from = {opacity: previous?.opacity ?? (isFeedback ? '.56' : '.74')};
    const to = {opacity: 1};
    if (isFeedback) {
      from.translate = previous?.translate ?? '0 3px';
      to.translate = '0 0';
    }
    animate(element, [from, to], isFeedback ? '--motion-feedback' : '--motion-enter');
  }

  function feedback(element, text) {
    if (element.textContent === text) return;
    element.textContent = text;
    if (text) reveal(element, 'feedback');
    else stop(element);
  }

  function reorder(container, selector, update) {
    if (reduce.matches || !container) return update();
    const positions = new Map();
    for (const row of container.querySelectorAll(selector)) {
      const box = row.getBoundingClientRect();
      if (box.bottom > 0 && box.top < innerHeight) positions.set(row.dataset.motionKey, box.top);
    }
    update();
    const moves = [];
    for (const row of container.querySelectorAll(selector)) {
      const top = positions.get(row.dataset.motionKey);
      if (top === undefined) continue;
      const offset = top - row.getBoundingClientRect().top;
      if (Math.abs(offset) > .5) moves.push([row, offset]);
    }
    for (const [row, offset] of moves) {
      animate(row, [{translate: '0 ' + offset + 'px'}, {translate:'0 0'}], '--motion-enter');
    }
  }

  function scrollTo(element, block = 'nearest') {
    element?.scrollIntoView({block, behavior: reduce.matches ? 'instant' : 'smooth'});
  }

  return {feedback, reveal, reorder, scrollTo, stop};
})();
