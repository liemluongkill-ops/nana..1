export const PROTOCOL_NAME = "nana.private-web-chat.v1";
export const CORE_CLOSE_CODES = Object.freeze({
  NORMAL: 1000, CORE_SHUTDOWN: 1001, INTERNAL_ERROR: 1011,
  HANDSHAKE_REQUIRED: 4001, CAPABILITY_REJECTED: 4002, ORIGIN_REJECTED: 4003,
  PROTOCOL_ERROR: 4004, SESSION_CAPACITY: 4005, HEARTBEAT_TIMEOUT: 4006,
  SLOW_CONSUMER: 4007, BRIDGE_DISABLED: 4008, EPOCH_CHANGED: 4009, WEB_TURN_STUCK: 4010,
});
export const CAPABILITIES = Object.freeze({
  chat_submit: true, reconcile: true, runtime_state: true,
  microphone: false, settings: false, model_select: false, cancel: false,
});
export const LIMITS = Object.freeze({
  heartbeat_ms: 5000, pong_timeout_ms: 10000, idle_timeout_ms: 20000,
  max_frame_bytes: 65536, max_user_text_chars: 4000, outbound_queue: 128,
});
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const CODE = /^[a-z][a-z0-9_]{0,63}$/;
export const TURN_STATES = new Set(["accepted", "thinking", "generated", "complete", "failed", "unknown"]);
export const VOICE_STATES = new Set(["idle", "queued", "speaking", "delivered", "failed", "unknown"]);
const ROOT = ["protocol", "server_epoch", "session_id", "turn_id", "correlation_id", "revision", "event_sequence", "type", "payload"];
const RUNTIME = ["type", "protocol", "server_epoch", "session_id", "revision", "core_status", "chat_available", "reason_code", "active_turn_state", "voice_state", "queue_depth", "capabilities"];
export function exact(value, keys, code = "protocol_error") {
  if (!value || typeof value !== "object" || Array.isArray(value)
      || Object.keys(value).sort().join(",") !== [...keys].sort().join(",")) throw new Error(code);
}
export function uuid(value, field = "uuid") {
  if (typeof value !== "string" || !UUID.test(value)) throw new Error("invalid_" + field);
  return value;
}
function integer(value, min = 0) {
  if (!Number.isSafeInteger(value) || value < min) throw new Error("invalid_revision");
}
function reason(value) {
  if (value !== null && (typeof value !== "string" || !CODE.test(value))) throw new Error("invalid_reason");
}
export function requireContext(context) {
  const result = {
    serverEpoch: context?.serverEpoch ?? context?.server_epoch,
    sessionId: context?.sessionId ?? context?.session_id,
    clientInstanceId: context?.clientInstanceId ?? context?.client_instance_id,
  };
  for (const [key, value] of Object.entries(result)) uuid(value, key);
  return result;
}
export function capabilities(value) {
  exact(value, Object.keys(CAPABILITIES));
  for (const key of Object.keys(CAPABILITIES)) if (value[key] !== CAPABILITIES[key]) throw new Error("invalid_capabilities");
}
export function buildFrame({context, turnId, correlationId, revision = 0, type, payload}) {
  const c = requireContext(context);
  uuid(turnId); uuid(correlationId); integer(revision);
  return {protocol: PROTOCOL_NAME, server_epoch: c.serverEpoch, session_id: c.sessionId,
    turn_id: turnId, correlation_id: correlationId, revision, type, payload};
}
export function buildChatSubmit({context, turnId, correlationId, text}) {
  if (typeof text !== "string" || !text.trim() || [...text].length > 4000) throw new Error("invalid_text");
  const normalized = text.trim().toLowerCase();
  if (normalized.startsWith("/") || ["exit", "thoat", "thoát", "dừng lại", "mở ai", "tắt ai"].includes(normalized)) throw new Error("admin_input_not_allowed");
  return buildFrame({context, turnId, correlationId, type: "chat.submit", payload: {text}});
}
export function buildReconcile({context, turnServerEpoch, turnId, correlationId, knownRevision}) {
  const c = requireContext(context);
  uuid(turnServerEpoch); integer(knownRevision);
  return buildFrame({context: c, turnId, correlationId, revision: knownRevision, type: "chat.reconcile",
    payload: {turn_server_epoch: turnServerEpoch, client_instance_id: c.clientInstanceId,
      turn_id: turnId, correlation_id: correlationId, revision: knownRevision}});
}
function statePayload(payload, states) {
  exact(payload, Object.hasOwn(payload, "reason_code") ? ["state", "reason_code"] : ["state"]);
  if (!states.has(payload.state)) throw new Error("invalid_state");
  if (Object.hasOwn(payload, "reason_code")) reason(payload.reason_code);
}
export function decodeCoreEvent(value, context) {
  const c = requireContext(context);
  if (!value || value.protocol !== PROTOCOL_NAME) throw new Error("protocol_mismatch");
  if (value.server_epoch !== c.serverEpoch) throw new Error("epoch_changed");
  if (value.session_id !== c.sessionId) throw new Error("session_mismatch");
  if (value.type === "session.ping") {
    exact(value, ["protocol", "type", "server_epoch", "session_id"]);
  } else if (value.type === "runtime.state") {
    exact(value, RUNTIME, "invalid_runtime_state");
    integer(value.revision, 1); reason(value.reason_code);
    if (!["ready", "busy", "shutting_down", "degraded"].includes(value.core_status)
        || typeof value.chat_available !== "boolean" || ![0, 1].includes(value.queue_depth)
        || (value.active_turn_state !== null && !TURN_STATES.has(value.active_turn_state))
        || !VOICE_STATES.has(value.voice_state)) throw new Error("invalid_runtime_state");
    capabilities(value.capabilities);
  } else {
    exact(value, ROOT);
    uuid(value.turn_id); uuid(value.correlation_id);
    integer(value.revision, 1); integer(value.event_sequence, 1);
    const p = value.payload;
    switch (value.type) {
      case "assistant.delta":
        exact(p, ["delta_index", "text"]); integer(p.delta_index, 1);
        if (typeof p.text !== "string" || !p.text) throw new Error("invalid_delta");
        break;
      case "assistant.final":
        exact(p, ["text"]);
        if (typeof p.text !== "string") throw new Error("invalid_final");
        break;
      case "turn.state": statePayload(p, TURN_STATES); break;
      case "voice.state": statePayload(p, VOICE_STATES); break;
      case "error":
        exact(p, ["code", "retryable"]); reason(p.code);
        if (!p.code || typeof p.retryable !== "boolean") throw new Error("invalid_error");
        break;
      case "turn.snapshot":
        exact(p, ["turn_server_epoch", "turn_state", "voice_state", "text", "delta_index", "reason_code"]);
        uuid(p.turn_server_epoch); integer(p.delta_index); reason(p.reason_code);
        if (!TURN_STATES.has(p.turn_state) || !VOICE_STATES.has(p.voice_state) || typeof p.text !== "string") throw new Error("invalid_snapshot");
        break;
      default: throw new Error("unsupported_core_event");
    }
  }
  return structuredClone(value);
}
export const decodeRuntimeState = decodeCoreEvent;

const AVATAR_ACTIONS = new Set([
  'wink_soft_smile', 'surprised_pout', 'serious_think', 'cat_teary_smile',
  'shy_crying', 'playful_wink', 'curious', 'happy', 'nod', 'listen',
  'think', 'surprised', 'shy', 'playful',
]);
const AVATAR_ROOT = ['protocol', 'type', 'server_epoch', 'session_id',
  'turn_id', 'correlation_id', 'visual_sequence', 'payload'];

function boundedInteger(value, min, max) {
  if (!Number.isSafeInteger(value) || value < min || value > max) throw new Error('invalid_avatar_bound');
}

export function decodeAvatarSignal(value, context) {
  exact(value, AVATAR_ROOT, 'invalid_avatar_schema');
  if (value.protocol !== PROTOCOL_NAME || value.type !== 'avatar.signal') throw new Error('invalid_avatar_protocol');
  const epoch = context?.serverEpoch ?? context?.server_epoch;
  const session = context?.sessionId ?? context?.session_id;
  uuid(epoch); uuid(session);
  if (value.server_epoch !== epoch || value.session_id !== session) throw new Error('avatar_context_mismatch');
  uuid(value.turn_id); uuid(value.correlation_id);
  integer(value.visual_sequence, 1);
  const p = value.payload;
  exact(p, ['playback_id', 'voice_state', 'expires_in_ms', 'mouth', 'expression']);
  uuid(p.playback_id);
  if (!['queued', 'speaking', 'delivered', 'failed', 'unknown'].includes(p.voice_state)) throw new Error('invalid_avatar_voice_state');
  boundedInteger(p.expires_in_ms, 0, 180);
  const mouth = p.mouth;
  exact(mouth, ['open', 'energy', 'viseme', 'speaking']);
  if (typeof mouth.open !== 'number' || !Number.isFinite(mouth.open) || mouth.open < 0 || mouth.open > 1
      || mouth.energy !== 0 || typeof mouth.speaking !== 'boolean'
      || !['aa', 'sil'].includes(mouth.viseme)) throw new Error('invalid_avatar_mouth');
  const silent = mouth.open === 0 && mouth.viseme === 'sil' && mouth.speaking === false;
  if (!silent && !(mouth.open > 0 && mouth.viseme === 'aa' && mouth.speaking && p.expires_in_ms > 0)) throw new Error('invalid_avatar_mouth_state');
  if (p.voice_state !== 'speaking' && (!silent || p.expires_in_ms !== 0 || p.expression !== null)) throw new Error('invalid_avatar_terminal');
  if (p.expression !== null) {
    exact(p.expression, ['event_id', 'action', 'duration_ms', 'expires_in_ms']);
    uuid(p.expression.event_id);
    if (!AVATAR_ACTIONS.has(p.expression.action)) throw new Error('invalid_avatar_action');
    boundedInteger(p.expression.duration_ms, 100, 4000);
    boundedInteger(p.expression.expires_in_ms, 1, 3500);
  }
  return structuredClone(value);
}
