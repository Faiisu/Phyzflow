export async function fetchConditionalJSON(url, { signal, etag, headers = {}, errorMessage } = {}) {
  const requestHeaders = { ...headers };
  if (etag) requestHeaders["If-None-Match"] = etag;
  const response = await fetch(url, {
    cache: "no-store",
    headers: requestHeaders,
    signal,
  });
  const nextEtag = response.headers.get("ETag") ?? etag ?? null;
  if (response.status === 304) {
    return { status: "unchanged", httpStatus: 304, etag: nextEtag, data: null };
  }
  const isJSON = response.headers.get("Content-Type")?.startsWith("application/json");
  const data = isJSON ? await response.json() : null;
  if (!response.ok) {
    const error = new Error(data?.error ?? errorMessage ?? `Request failed (${response.status}).`);
    error.httpStatus = response.status;
    throw error;
  }
  return { status: "changed", httpStatus: response.status, etag: nextEtag, data };
}

export function createLiveSync({
  fetchUpdate,
  onUpdate,
  onUnchanged = () => {},
  onError = () => {},
  errorDelayMs = 1000,
  pauseWhenHidden = true,
  initialEtag = null,
}) {
  let started = false;
  let active = true;
  let timer = null;
  let controller = null;
  let inFlight = false;
  let wakeQueued = false;
  let etag = initialEtag;

  function canRun() {
    return started && active && (!pauseWhenHidden || !document.hidden);
  }

  function clearTimer() {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  }

  function abortCurrent() {
    if (controller) {
      controller.abort();
      controller = null;
    }
  }

  function schedule(delayMs) {
    clearTimer();
    if (!canRun()) return;
    timer = setTimeout(() => {
      timer = null;
      void tick();
    }, Math.max(0, delayMs));
  }

  async function tick() {
    if (!canRun()) return;
    if (inFlight) {
      wakeQueued = true;
      return;
    }
    clearTimer();
    inFlight = true;
    wakeQueued = false;
    const current = new AbortController();
    controller = current;
    let nextDelayMs = errorDelayMs;
    try {
      const result = await fetchUpdate({ signal: current.signal, etag });
      if (current.signal.aborted || !canRun()) return;
      if (result.stop) {
        stop();
        return;
      }
      if (result.etag !== undefined) etag = result.etag;
      if (result.status === "changed") onUpdate(result.data);
      else onUnchanged();
      nextDelayMs = typeof result.nextDelayMs === "function" ? result.nextDelayMs() : result.nextDelayMs;
    } catch (error) {
      if (!current.signal.aborted) onError(error);
    } finally {
      if (controller === current) controller = null;
      inFlight = false;
      if (canRun()) schedule(wakeQueued ? 0 : nextDelayMs);
    }
  }

  function onVisibilityChange() {
    if (canRun()) wake();
    else {
      clearTimer();
      abortCurrent();
    }
  }

  function start({ immediate = true, delayMs = 0 } = {}) {
    if (started) return;
    started = true;
    if (pauseWhenHidden) document.addEventListener("visibilitychange", onVisibilityChange);
    if (canRun()) {
      if (immediate) void tick();
      else schedule(delayMs);
    }
  }

  function stop() {
    if (!started) return;
    started = false;
    clearTimer();
    abortCurrent();
    if (pauseWhenHidden) document.removeEventListener("visibilitychange", onVisibilityChange);
  }

  function setActive(nextActive) {
    const normalized = Boolean(nextActive);
    if (active === normalized) return;
    active = normalized;
    if (canRun()) wake();
    else {
      clearTimer();
      abortCurrent();
    }
  }

  function wake() {
    if (!canRun()) return;
    clearTimer();
    if (inFlight) wakeQueued = true;
    else void tick();
  }

  function resetEtag() {
    etag = null;
  }

  return { start, stop, setActive, wake, resetEtag };
}
