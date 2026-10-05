export const PUBLIC_SIGNAL_PROTOCOL = "nana.public-visual.v1";
export const PUBLIC_SIGNAL_ENDPOINT = "http://127.0.0.1:8766/v1/avatar/public-signals";
export const UNITY_RUNTIME_OBJECT = "NanaTargetDrivenRuntime";

const MOUTH_STALE_AFTER_MS = 180;
const MAX_EXPRESSION_EXPIRES_MS = 5000;
const MAX_TRACKED_IDENTITIES = 256;
const DEFAULT_POLL_INTERVAL_MS = 40;
const DEFAULT_REQUEST_TIMEOUT_MS = 400;
const DEFAULT_MAX_FAILURE_DELAY_MS = 1000;

const ROOT_KEYS = Object.freeze([
  "current",
  "cursor",
  "ok",
  "protocol",
  "stale_after_ms",
]);

const SIGNAL_KEYS = Object.freeze([
  "age_ms",
  "attempt_id",
  "energy",
  "event_id",
  "expires_in_ms",
  "expression",
  "open",
  "playback_id",
  "sequence",
  "speaking",
  "stream_session_id",
  "terminal_reason",
  "viseme",
]);

const EXPRESSION_KEYS = Object.freeze(["action", "event_id", "expires_in_ms"]);

const VISEMES = new Set(["sil", "aa", "ih", "ee", "oh", "ou", "nn"]);
const EXPRESSION_ACTIONS = new Set([
  "curious",
  "happy",
  "nod",
  "listen",
  "think",
  "surprised",
  "shy",
  "playful",
  "wink_soft_smile",
  "surprised_pout",
  "serious_think",
  "cat_teary_smile",
  "shy_crying",
  "playful_wink",
  "settle",
  "idle",
  "blink",
]);
const TERMINAL_REASONS = new Set(["completed", "cancelled", "failed", "shutdown"]);

function isRecord(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function hasExactKeys(value, expected) {
  if (!isRecord(value)) return false;
  const actual = Object.keys(value).sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function isBoundedId(value) {
  return typeof value === "string"
    && value.length >= 1
    && value.length <= 256
    && value === value.trim()
    && !/\p{Cc}/u.test(value);
}

function isIntegerInRange(value, minimum, maximum) {
  return Number.isInteger(value) && value >= minimum && value <= maximum;
}

function isUnitNumber(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1;
}

function validateExpression(value) {
  if (value === null) return true;
  return hasExactKeys(value, EXPRESSION_KEYS)
    && isBoundedId(value.event_id)
    && EXPRESSION_ACTIONS.has(value.action)
    && isIntegerInRange(value.expires_in_ms, 0, MAX_EXPRESSION_EXPIRES_MS);
}

function validateSignal(value) {
  if (!hasExactKeys(value, SIGNAL_KEYS)) return false;
  if (!isBoundedId(value.stream_session_id)
    || !isBoundedId(value.playback_id)
    || !isBoundedId(value.attempt_id)
    || !isBoundedId(value.event_id)) return false;
  if (!Number.isInteger(value.sequence) || value.sequence <= 0) return false;
  if (!isUnitNumber(value.open) || !isUnitNumber(value.energy)) return false;
  if (!VISEMES.has(value.viseme) || typeof value.speaking !== "boolean") return false;
  if (!isIntegerInRange(value.age_ms, 0, MOUTH_STALE_AFTER_MS)
    || !isIntegerInRange(value.expires_in_ms, 0, MOUTH_STALE_AFTER_MS)
    || value.age_ms + value.expires_in_ms > MOUTH_STALE_AFTER_MS) return false;
  if (!validateExpression(value.expression)) return false;
  if (value.terminal_reason !== null && !TERMINAL_REASONS.has(value.terminal_reason)) return false;
  if (value.terminal_reason !== null) {
    return value.open === 0
      && value.energy === 0
      && value.viseme === "sil"
      && value.speaking === false
      && value.expression === null;
  }
  if (!value.speaking && (value.open !== 0 || value.energy !== 0 || value.viseme !== "sil")) {
    return false;
  }
  return true;
}

function validateEnvelope(value) {
  return hasExactKeys(value, ROOT_KEYS)
    && value.ok === true
    && value.protocol === PUBLIC_SIGNAL_PROTOCOL
    && Number.isInteger(value.cursor)
    && value.cursor >= 0
    && value.stale_after_ms === MOUTH_STALE_AFTER_MS
    && (value.current === null || validateSignal(value.current));
}

function boundedSetAdd(set, value) {
  if (set.has(value)) return true;
  if (set.size >= MAX_TRACKED_IDENTITIES) return false;
  set.add(value);
  return true;
}

function identityFor(signal) {
  return [
    signal.stream_session_id,
    signal.playback_id,
    signal.attempt_id,
    signal.event_id,
  ].join("\u001f");
}

function playbackFor(signal) {
  return [signal.stream_session_id, signal.playback_id].join("\u001f");
}

function silentMouthPayload() {
  return { open: 0, energy: 0, viseme: "sil", speaking: false };
}

export function createObsSignalConsumer({
  fetchImpl = globalThis.fetch?.bind(globalThis),
  sendUnity,
  setTimer = globalThis.setTimeout?.bind(globalThis),
  clearTimer = globalThis.clearTimeout?.bind(globalThis),
  createAbortController = () => new AbortController(),
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
  requestTimeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
  maxFailureDelayMs = DEFAULT_MAX_FAILURE_DELAY_MS,
  onConnectionChange = () => {},
} = {}) {
  if (typeof fetchImpl !== "function") throw new TypeError("fetch_required");
  if (typeof sendUnity !== "function") throw new TypeError("send_unity_required");
  if (typeof setTimer !== "function" || typeof clearTimer !== "function") {
    throw new TypeError("timers_required");
  }

  let cursor = 0;
  let active = null;
  let running = false;
  let inFlight = false;
  let lifecycleGeneration = 0;
  let replayTrackingExhausted = false;
  let connected = false;
  let failureDelayMs = pollIntervalMs;
  let pollTimer = null;
  let expiryTimer = null;
  let requestController = null;
  const retiredPlaybacks = new Set();
  const retiredSessions = new Set();
  const seenExpressions = new Set();

  function setConnected(value) {
    if (connected === value) return;
    connected = value;
    onConnectionChange(value);
  }

  function clearExpiry() {
    if (expiryTimer === null) return;
    clearTimer(expiryTimer);
    expiryTimer = null;
  }

  function dispatchUnity(method, payload) {
    try {
      return sendUnity(UNITY_RUNTIME_OBJECT, method, JSON.stringify(payload)) !== false;
    } catch {
      return false;
    }
  }

  function sendMouth(payload) {
    return dispatchUnity("SetMouthVisemeJson", payload);
  }

  function closeMouth() {
    clearExpiry();
    return sendMouth(silentMouthPayload());
  }

  function retireActive({ retireSession = false } = {}) {
    if (!active) return true;
    const playbackRemembered = boundedSetAdd(retiredPlaybacks, active.playbackKey);
    const sessionRemembered = !retireSession
      || boundedSetAdd(retiredSessions, active.streamSessionId);
    active = null;
    if (!playbackRemembered || !sessionRemembered) replayTrackingExhausted = true;
    return !replayTrackingExhausted;
  }

  function rejectCurrent() {
    closeMouth();
    return false;
  }

  function applyCurrent(signal, responseCursor) {
    if (replayTrackingExhausted) return rejectCurrent();
    if (responseCursor === cursor) return rejectCurrent();

    const identity = identityFor(signal);
    const playbackKey = playbackFor(signal);

    if (retiredSessions.has(signal.stream_session_id)
      || retiredPlaybacks.has(playbackKey)
      || (active?.playbackKey === playbackKey && active.identity !== identity)
      || (active?.identity === identity && signal.sequence <= active.sequence)) {
      return rejectCurrent();
    }

    if (active && active.streamSessionId !== signal.stream_session_id) {
      if (!retireActive({ retireSession: true })) return rejectCurrent();
    } else if (active && active.playbackKey !== playbackKey) {
      if (!retireActive()) return rejectCurrent();
    }

    cursor = responseCursor;
    active = {
      identity,
      playbackKey,
      sequence: signal.sequence,
      streamSessionId: signal.stream_session_id,
    };

    if (signal.terminal_reason !== null) {
      const delivered = closeMouth();
      const tracked = retireActive();
      return delivered && tracked;
    }

    if (signal.expires_in_ms === 0) return rejectCurrent();

    const expression = signal.expression;
    let dispatchExpression = false;
    if (expression !== null && !seenExpressions.has(expression.event_id)) {
      if (!boundedSetAdd(seenExpressions, expression.event_id)) {
        replayTrackingExhausted = true;
        return rejectCurrent();
      }
      dispatchExpression = expression.expires_in_ms > 0;
    }

    if (!sendMouth({
      open: signal.open,
      energy: signal.energy,
      viseme: signal.viseme,
      speaking: signal.speaking,
    })) {
      closeMouth();
      return false;
    }
    clearExpiry();
    expiryTimer = setTimer(() => {
      expiryTimer = null;
      sendMouth(silentMouthPayload());
    }, signal.expires_in_ms);

    if (dispatchExpression && !dispatchUnity("ReceiveAvatarIntentJson", {
      intent_id: expression.event_id,
      action: expression.action,
      duration_s: expression.expires_in_ms / 1000,
    })) {
      closeMouth();
      return false;
    }
    return true;
  }

  function applyEnvelope(value) {
    if (!validateEnvelope(value)) return rejectCurrent();
    if (value.current !== null) return applyCurrent(value.current, value.cursor);
    if (value.cursor === cursor) return true;
    cursor = value.cursor;
    const delivered = closeMouth();
    const tracked = retireActive();
    return delivered && tracked;
  }

  function transportFailure() {
    setConnected(false);
    cursor = 0;
    closeMouth();
    return false;
  }

  async function pollOnce(expectedGeneration = null) {
    if (expectedGeneration !== null && expectedGeneration !== lifecycleGeneration) return false;
    if (inFlight) return false;
    inFlight = true;
    requestController = createAbortController();
    const timeout = setTimer(() => requestController?.abort(), requestTimeoutMs);
    try {
      const response = await fetchImpl(`${PUBLIC_SIGNAL_ENDPOINT}?after=${cursor}`, {
        method: "GET",
        cache: "no-store",
        credentials: "omit",
        headers: { Accept: "application/json" },
        signal: requestController.signal,
      });
      if (!response?.ok || response.status !== 200) return transportFailure();
      const value = await response.json();
      if (expectedGeneration !== null && expectedGeneration !== lifecycleGeneration) return false;
      const accepted = applyEnvelope(value);
      setConnected(accepted);
      return accepted;
    } catch {
      if (expectedGeneration !== null && expectedGeneration !== lifecycleGeneration) return false;
      return transportFailure();
    } finally {
      clearTimer(timeout);
      requestController = null;
      inFlight = false;
    }
  }

  async function pollLoop(generation) {
    if (!running || generation !== lifecycleGeneration) return;
    const accepted = await pollOnce(generation);
    if (!running || generation !== lifecycleGeneration) return;
    failureDelayMs = accepted
      ? pollIntervalMs
      : Math.min(maxFailureDelayMs, Math.max(pollIntervalMs, failureDelayMs * 2));
    pollTimer = setTimer(() => {
      pollTimer = null;
      void pollLoop(generation);
    }, failureDelayMs);
  }

  function start() {
    if (running) return;
    running = true;
    const generation = ++lifecycleGeneration;
    failureDelayMs = pollIntervalMs;
    void pollLoop(generation);
  }

  function stop() {
    running = false;
    lifecycleGeneration += 1;
    if (pollTimer !== null) {
      clearTimer(pollTimer);
      pollTimer = null;
    }
    requestController?.abort();
    requestController = null;
    setConnected(false);
    closeMouth();
  }

  function snapshot() {
    return {
      active: active?.identity ?? null,
      connected,
      cursor,
      running,
    };
  }

  return Object.freeze({ pollOnce, snapshot, start, stop });
}

export function bindObsSignalLifecycle({
  consumer,
  documentTarget = globalThis.document,
  windowTarget = globalThis.window,
} = {}) {
  if (!consumer || typeof consumer.start !== "function" || typeof consumer.stop !== "function") {
    throw new TypeError("consumer_required");
  }
  if (!documentTarget || !windowTarget) throw new TypeError("event_targets_required");

  let disposed = false;

  function handleVisibility() {
    if (disposed) return;
    if (documentTarget.visibilityState === "hidden") consumer.stop();
    else consumer.start();
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    documentTarget.removeEventListener("visibilitychange", handleVisibility);
    windowTarget.removeEventListener("pagehide", dispose);
    consumer.stop();
  }

  documentTarget.addEventListener("visibilitychange", handleVisibility);
  windowTarget.addEventListener("pagehide", dispose);
  handleVisibility();
  return dispose;
}
