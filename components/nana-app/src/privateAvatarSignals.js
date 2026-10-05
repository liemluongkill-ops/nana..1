import { decodeAvatarSignal, uuid } from "./coreChatProtocol.js";

export const UNITY_RUNTIME_OBJECT = "NanaTargetDrivenRuntime";

const TERMINAL_VOICE_STATES = new Set(["delivered", "failed", "unknown"]);
const MAX_TRACKED_IDS = 256;
const SETTLE_DURATION_SECONDS = 0.18;
const SILENT_MOUTH = Object.freeze({ open: 0, energy: 0, viseme: "sil", speaking: false });

function boundedSetAdd(set, value) {
  if (set.has(value)) return true;
  if (set.size >= MAX_TRACKED_IDS) return false;
  set.add(value);
  return true;
}

function normalizeContext(value) {
  const result = {
    serverEpoch: value?.serverEpoch ?? null,
    sessionId: value?.sessionId ?? null,
    turnId: value?.turnId ?? null,
    correlationId: value?.correlationId ?? null,
  };
  try {
    for (const [field, id] of Object.entries(result)) uuid(id, field);
  } catch {
    return null;
  }
  return result;
}

function sameContext(left, right) {
  return left !== null && right !== null
    && left.serverEpoch === right.serverEpoch
    && left.sessionId === right.sessionId
    && left.turnId === right.turnId
    && left.correlationId === right.correlationId;
}

function trackingContext(value) {
  if (value === null) return null;
  return {
    serverEpoch: value.serverEpoch,
    turnId: value.turnId,
    correlationId: value.correlationId,
  };
}

function sameTrackingContext(left, right) {
  return left !== null && right !== null
    && left.serverEpoch === right.serverEpoch
    && left.turnId === right.turnId
    && left.correlationId === right.correlationId;
}

export function createPrivateAvatarSignalConsumer({
  sendUnity,
  setTimer = globalThis.setTimeout?.bind(globalThis),
  clearTimer = globalThis.clearTimeout?.bind(globalThis),
  randomUuid = () => globalThis.crypto?.randomUUID?.(),
} = {}) {
  if (typeof sendUnity !== "function") throw new TypeError("send_unity_required");
  if (typeof setTimer !== "function" || typeof clearTimer !== "function") {
    throw new TypeError("timers_required");
  }

  let context = null;
  let replayContext = null;
  let connected = false;
  let visible = true;
  let unityReady = false;
  let stopped = false;
  let turnClosed = false;
  let activePlayback = null;
  let activeSequence = 0;
  let mouthTimer = null;
  let expressionTimer = null;
  let activeExpression = null;
  const retiredPlaybacks = new Set();
  const seenExpressions = new Set();

  function clearMouthTimer() {
    if (mouthTimer === null) return;
    clearTimer(mouthTimer);
    mouthTimer = null;
  }

  function clearExpressionTimer() {
    if (expressionTimer === null) return;
    clearTimer(expressionTimer);
    expressionTimer = null;
  }

  function dispatch(method, payload) {
    if (!unityReady) return false;
    try {
      return sendUnity(UNITY_RUNTIME_OBJECT, method, JSON.stringify(payload)) !== false;
    } catch {
      return false;
    }
  }

  function closeMouth() {
    clearMouthTimer();
    return unityReady ? dispatch("SetMouthVisemeJson", SILENT_MOUTH) : true;
  }

  function settleOwnedExpression() {
    clearExpressionTimer();
    if (activeExpression === null) return true;
    activeExpression = null;
    let intentId;
    try { intentId = randomUuid(); } catch { return false; }
    try { uuid(intentId, "settle_intent_id"); } catch { return false; }
    return unityReady ? dispatch("ReceiveAvatarIntentJson", {
      intent_id: intentId,
      action: "settle",
      duration_s: SETTLE_DURATION_SECONDS,
    }) : true;
  }

  function closeOwnedOutputs() {
    const mouthClosed = closeMouth();
    const expressionSettled = settleOwnedExpression();
    return mouthClosed && expressionSettled;
  }

  function resetTurnTracking() {
    activePlayback = null;
    activeSequence = 0;
    turnClosed = false;
    retiredPlaybacks.clear();
    seenExpressions.clear();
  }

  function retireActivePlayback() {
    if (activePlayback === null) return true;
    const remembered = boundedSetAdd(retiredPlaybacks, activePlayback);
    activePlayback = null;
    activeSequence = 0;
    return remembered;
  }

  function rejectSignal() {
    closeMouth();
    return false;
  }

  function snapshot() {
    return {
      connected,
      visible,
      unityReady,
      stopped,
      turnId: context?.turnId ?? null,
      correlationId: context?.correlationId ?? null,
      activePlaybackId: activePlayback,
      visualSequence: activeSequence,
    };
  }

  function syncContext(next = {}) {
    const nextContext = normalizeContext(next);
    const nextReplayContext = trackingContext(nextContext);
    const replayChanged = nextReplayContext !== null
      && !sameTrackingContext(replayContext, nextReplayContext);
    if (!sameContext(context, nextContext)) closeOwnedOutputs();
    if (replayChanged) {
      replayContext = nextReplayContext;
      closeOwnedOutputs();
      resetTurnTracking();
    }
    context = nextContext;

    connected = next.connected === true && context !== null;
    if (!connected) closeOwnedOutputs();
    if (TERMINAL_VOICE_STATES.has(next.voiceState)) {
      closeOwnedOutputs();
      retireActivePlayback();
      turnClosed = true;
    }
    return snapshot();
  }

  function setUnityReady(nextReady) {
    const ready = nextReady === true;
    if (!ready && unityReady) closeOwnedOutputs();
    unityReady = ready;
    return unityReady;
  }

  function setVisible(nextVisible) {
    const next = nextVisible === true;
    if (!next && visible) closeOwnedOutputs();
    visible = next;
    return visible;
  }

  function accept(raw) {
    if (context === null || turnClosed || stopped) return rejectSignal();
    let signal;
    try { signal = decodeAvatarSignal(raw, context); } catch { return rejectSignal(); }
    if (signal.turn_id !== context.turnId || signal.correlation_id !== context.correlationId) {
      return rejectSignal();
    }

    const playbackId = signal.payload.playback_id;
    if (retiredPlaybacks.has(playbackId)) return rejectSignal();
    if (activePlayback === playbackId) {
      if (signal.visual_sequence <= activeSequence) return rejectSignal();
    } else {
      if (activePlayback !== null) {
        closeOwnedOutputs();
        if (!retireActivePlayback()) return rejectSignal();
      }
      activePlayback = playbackId;
      activeSequence = 0;
    }
    activeSequence = signal.visual_sequence;

    if (!connected || !visible || !unityReady) return rejectSignal();
    const payload = signal.payload;
    if (payload.voice_state === "queued") return closeMouth();
    if (TERMINAL_VOICE_STATES.has(payload.voice_state)) {
      const closed = closeOwnedOutputs();
      const retired = retireActivePlayback();
      turnClosed = true;
      return closed && retired;
    }

    const mouthDelivered = payload.expires_in_ms === 0
      ? closeMouth()
      : dispatch("SetMouthVisemeJson", payload.mouth);
    if (!mouthDelivered) {
      closeMouth();
      return false;
    }
    clearMouthTimer();
    if (payload.expires_in_ms > 0) {
      mouthTimer = setTimer(() => {
        mouthTimer = null;
        if (unityReady) dispatch("SetMouthVisemeJson", SILENT_MOUTH);
      }, payload.expires_in_ms);
    }

    const expression = payload.expression;
    if (expression !== null && !seenExpressions.has(expression.event_id)) {
      if (!boundedSetAdd(seenExpressions, expression.event_id)) return rejectSignal();
      const durationMs = Math.min(expression.duration_ms, expression.expires_in_ms);
      if (!dispatch("ReceiveAvatarIntentJson", {
        intent_id: expression.event_id,
        action: expression.action,
        duration_s: durationMs / 1000,
      })) {
        closeMouth();
        return false;
      }
      clearExpressionTimer();
      activeExpression = expression.event_id;
      expressionTimer = setTimer(() => {
        expressionTimer = null;
        activeExpression = null;
      }, durationMs);
    }
    return true;
  }

  function stop() {
    if (stopped) return;
    closeOwnedOutputs();
    stopped = true;
    connected = false;
    context = null;
    replayContext = null;
    resetTurnTracking();
  }

  return Object.freeze({ accept, setUnityReady, setVisible, snapshot, stop, syncContext });
}
