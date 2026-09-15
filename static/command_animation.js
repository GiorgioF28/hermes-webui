/* Pause decorative WebGL scenes when not visible and cap rendering to 24 FPS. */
export function scheduleVisibleAnimation(container, draw) {
  let visible = false, raf = null, last = -Infinity, stopped = false;
  const interval = 1000 / 24;
  function stop() {
    stopped = true;
    if (raf !== null) cancelAnimationFrame(raf);
    raf = null;
    if (observer) observer.disconnect();
    document.removeEventListener('visibilitychange', resume);
  }
  function frame(now) {
    raf = null;
    if (stopped) return;
    if (!container.isConnected) { stop(); return; }
    if (document.hidden || !visible) return;
    if (now - last >= interval) { last = now; draw(); }
    raf = requestAnimationFrame(frame);
  }
  function resume() {
    if (!stopped && !document.hidden && visible && raf === null) raf = requestAnimationFrame(frame);
  }
  const observer = typeof IntersectionObserver === 'function' ? new IntersectionObserver(entries => {
    visible = entries.some(entry => entry.isIntersecting);
    resume();
  }) : null;
  if (observer) observer.observe(container);
  else { visible = true; resume(); }
  document.addEventListener('visibilitychange', resume);
  return stop;
}

