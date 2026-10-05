import {decodeCoreEvent, requireContext, PROTOCOL_NAME} from "./coreChatProtocol.js";

const TURN_NEXT = {
  idle: ["accepted", "thinking", "generated", "complete", "failed", "unknown"],
  accepted: ["thinking", "generated", "complete", "failed", "unknown"],
  thinking: ["generated", "complete", "failed", "unknown"],
  generated: ["complete", "failed", "unknown"],
};
const VOICE_NEXT = {idle: ["queued", "failed", "unknown"], queued: ["speaking", "failed", "unknown"], speaking: ["delivered", "failed", "unknown"]};

export function createConversationController({onAction = () => {}, context: initialContext = null} = {}) {
  let context = initialContext;
  let state = {
    protocol: PROTOCOL_NAME, connectionState: "disconnected", uncertain: false,
    revision: 0, activeTurn: null, turnState: "idle",
    voiceState: "idle", runtimeState: null, lastError: null,
    microphone: {status: "unavailable", permission: "prompt"},
  };
  let deltaIndex = 0;
  let finalSeen = false;
  const snapshot = () => structuredClone(state);
  function emit(action) {
    const result = {action, snapshot: snapshot()};
    onAction(result);
    return result;
  }
  function epochChanged() {
    state = {...state, connectionState: "unknown", uncertain: true,
      turnState: "unknown", voiceState: "unknown", lastError: {code: "epoch_changed", retryable: false}};
    return emit("epoch_changed");
  }
  function beginTurn({turnId, correlationId}) {
    state = {...state, revision: 0, turnState: "idle", voiceState: "idle",
      activeTurn: {turnId, correlationId, text: ""}, lastError: null, uncertain: false};
    deltaIndex = 0; finalSeen = false;
  }
  function apply(raw, nextContext = context) {
    let e;
    try { e = decodeCoreEvent(raw, nextContext); }
    catch (error) {
      return error.message === "epoch_changed" ? epochChanged() : emit("protocol_error");
    }
    context = requireContext(nextContext);
    if (e.type === "session.ping") return emit("heartbeat");
    if (e.type === "runtime.state") {
      state.runtimeState = e; return emit("runtime");
    }
    const active = state.activeTurn;
    if (active && (e.turn_id !== active.turnId || e.correlation_id !== active.correlationId)) return emit("ignored");
    if (e.type === "turn.snapshot" && e.payload.reason_code === "epoch_changed") return epochChanged();
    if (e.revision <= state.revision) return emit("ignored");
    if (e.type !== "turn.snapshot" && e.type !== "assistant.final"
        && e.revision !== state.revision + 1) return emit("reconcile");
    if (e.type === "assistant.delta" && (finalSeen || e.payload.delta_index !== deltaIndex + 1)) return emit("reconcile");
    if (e.type === "turn.state" && e.payload.state !== state.turnState
        && !(TURN_NEXT[state.turnState] || []).includes(e.payload.state)) return emit("ignored");
    if (e.type === "voice.state" && e.payload.state !== state.voiceState
        && !(VOICE_NEXT[state.voiceState] || []).includes(e.payload.state)) return emit("ignored");
    if (!state.activeTurn) state.activeTurn = {turnId: e.turn_id, correlationId: e.correlation_id, text: ""};
    switch (e.type) {
      case "assistant.delta": state.activeTurn.text += e.payload.text; deltaIndex = e.payload.delta_index; break;
      case "assistant.final": state.activeTurn.text = e.payload.text; finalSeen = true; break;
      case "turn.state": state.turnState = e.payload.state; break;
      case "voice.state": state.voiceState = e.payload.state; break;
      case "turn.snapshot":
        state.activeTurn.text = e.payload.text;
        state.turnState = e.payload.turn_state;
        state.voiceState = e.payload.voice_state;
        state.uncertain = false;
        deltaIndex = e.payload.delta_index;
        finalSeen = ["generated", "complete"].includes(state.turnState);
        break;
      case "error": state.lastError = e.payload; break;
    }
    state.revision = e.revision;
    return emit("applied");
  }
  return Object.freeze({
    apply, snapshot, beginTurn,
    connect(nextContext) {
      const next = requireContext(nextContext);
      if (context && context.serverEpoch !== next.serverEpoch && state.activeTurn) {
        context = next; return epochChanged();
      }
      context = next; state.connectionState = "connected"; return emit("connected");
    },
    disconnect() {
      state.connectionState = "disconnected";
      state.uncertain = !!state.activeTurn;
      return emit("disconnected");
    },
    reset(nextContext) {
      context = nextContext;
      state = {...state, activeTurn: null, revision: 0,
        turnState: "idle", voiceState: "idle", uncertain: false, lastError: null};
      deltaIndex = 0; finalSeen = false; return emit("reset");
    },
  });
}
