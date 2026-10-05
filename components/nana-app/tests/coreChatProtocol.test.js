import assert from "node:assert/strict";
import test from "node:test";

import {
  PROTOCOL_NAME,
  buildChatSubmit,
  buildReconcile,
  decodeAvatarSignal,
  decodeCoreEvent,
} from "../src/coreChatProtocol.js";

const CONTEXT = Object.freeze({
  serverEpoch: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  sessionId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  clientInstanceId: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
});
const TURN = "11111111-1111-4111-8111-111111111111";
const CORRELATION = "22222222-2222-4222-8222-222222222222";
const PLAYBACK = "33333333-3333-4333-8333-333333333333";
const EXPRESSION = "44444444-4444-4444-8444-444444444444";

function avatarSignal(overrides = {}) {
  const base = {
    protocol: PROTOCOL_NAME,
    type: "avatar.signal",
    server_epoch: CONTEXT.serverEpoch,
    session_id: CONTEXT.sessionId,
    turn_id: TURN,
    correlation_id: CORRELATION,
    visual_sequence: 1,
    payload: {
      playback_id: PLAYBACK,
      voice_state: "speaking",
      expires_in_ms: 160,
      mouth: { open: 0.6, energy: 0, viseme: "aa", speaking: true },
      expression: {
        event_id: EXPRESSION,
        action: "happy",
        duration_ms: 1200,
        expires_in_ms: 900,
      },
    },
  };
  return {
    ...base,
    ...overrides,
    payload: { ...base.payload, ...(overrides.payload || {}) },
  };
}

test("builds exact chat submit and reconcile envelopes", () => {
  const submit = buildChatSubmit({ context: CONTEXT, turnId: TURN, correlationId: CORRELATION, text: "Chao Nana" });
  assert.deepEqual(submit, {
    protocol: PROTOCOL_NAME,
    server_epoch: CONTEXT.serverEpoch,
    session_id: CONTEXT.sessionId,
    turn_id: TURN,
    correlation_id: CORRELATION,
    revision: 0,
    type: "chat.submit",
    payload: { text: "Chao Nana" },
  });
  const reconcile = buildReconcile({
    context: CONTEXT,
    turnServerEpoch: CONTEXT.serverEpoch,
    turnId: TURN,
    correlationId: CORRELATION,
    knownRevision: 4,
  });
  assert.equal(reconcile.type, "chat.reconcile");
  assert.equal(reconcile.payload.turn_server_epoch, CONTEXT.serverEpoch);
  assert.deepEqual(reconcile.payload, {
    turn_server_epoch: CONTEXT.serverEpoch, client_instance_id: CONTEXT.clientInstanceId,
    turn_id: TURN, correlation_id: CORRELATION, revision: 4,
  });
  assert.equal(reconcile.revision, 4);
});

test("decodes indexed assistant delta with exact envelope identity", () => {
  const event = decodeCoreEvent({
    protocol: PROTOCOL_NAME,
    server_epoch: CONTEXT.serverEpoch,
    session_id: CONTEXT.sessionId,
    turn_id: TURN,
    correlation_id: CORRELATION,
    revision: 2,
    event_sequence: 2,
    type: "assistant.delta",
    payload: { delta_index: 1, text: "Chao " },
  }, CONTEXT);
  assert.equal(event.type, "assistant.delta");
  assert.equal(event.payload.delta_index, 1);
});

test("rejects old epoch, non-monotonic and secret-bearing runtime events", () => {
  assert.throws(() => decodeCoreEvent({
    protocol: PROTOCOL_NAME,
    server_epoch: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    session_id: CONTEXT.sessionId,
    turn_id: TURN,
    correlation_id: CORRELATION,
    revision: 1,
    event_sequence: 1,
    type: "turn.state",
    payload: { state: "accepted" },
  }, CONTEXT), /epoch/);
  assert.throws(() => decodeCoreEvent({
    protocol: PROTOCOL_NAME,
    server_epoch: CONTEXT.serverEpoch,
    session_id: CONTEXT.sessionId,
    turn_id: TURN,
    correlation_id: CORRELATION,
    revision: 0,
    event_sequence: 1,
    type: "runtime.state",
    payload: { provider: "secret" },
  }, CONTEXT), /revision|runtime/);
});

test("runtime state accepts only the redacted allowlist", () => {
  const state = decodeCoreEvent({
    protocol: PROTOCOL_NAME,
    server_epoch: CONTEXT.serverEpoch,
    session_id: CONTEXT.sessionId,
    revision: 1,
    type: "runtime.state",
      core_status: "ready",
      chat_available: true,
      reason_code: null,
      active_turn_state: null,
      voice_state: "idle",
      queue_depth: 0,
      capabilities: {
        chat_submit: true, reconcile: true, runtime_state: true,
        microphone: false, settings: false, model_select: false, cancel: false,
      },
  }, CONTEXT);
  assert.equal(state.type, "runtime.state");
  assert.equal(state.core_status, "ready");
});

test('extra payload fields and enabled unsupported capabilities fail closed', () => {
  const e = {protocol: PROTOCOL_NAME, server_epoch: CONTEXT.serverEpoch, session_id: CONTEXT.sessionId,
    turn_id: TURN, correlation_id: CORRELATION, revision: 1, event_sequence: 1,
    type: 'assistant.final', payload: {text: 'fixture', private_data: 'forbidden'}};
  assert.throws(() => decodeCoreEvent(e, CONTEXT));
});

test("decodes the exact private avatar envelope without chat ordering fields", () => {
  const signal = avatarSignal();

  assert.deepEqual(decodeAvatarSignal(signal, CONTEXT), signal);
  assert.equal(Object.hasOwn(signal, "revision"), false);
  assert.equal(Object.hasOwn(signal, "event_sequence"), false);
});

test("private avatar schema rejects wrong context, extra fields, and out-of-range payloads", () => {
  const invalid = [
    avatarSignal({ server_epoch: "dddddddd-dddd-4ddd-8ddd-dddddddddddd" }),
    avatarSignal({ session_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd" }),
    avatarSignal({ revision: 1 }),
    avatarSignal({ visual_sequence: 0 }),
    avatarSignal({ payload: { playback_id: "not-a-uuid" } }),
    avatarSignal({ payload: { voice_state: "idle" } }),
    avatarSignal({ payload: { expires_in_ms: 181 } }),
    avatarSignal({ payload: { mouth: { open: 0.5, energy: 0.1, viseme: "aa", speaking: true } } }),
    avatarSignal({ payload: { mouth: { open: 0.5, energy: 0, viseme: "ih", speaking: true } } }),
    avatarSignal({ payload: { expression: { event_id: EXPRESSION, action: "wave", duration_ms: 1200, expires_in_ms: 900 } } }),
    avatarSignal({ payload: { expression: { event_id: EXPRESSION, action: "happy", duration_ms: 99, expires_in_ms: 900 } } }),
    avatarSignal({ payload: { expression: { event_id: EXPRESSION, action: "happy", duration_ms: 1200, expires_in_ms: 0 } } }),
  ];

  for (const value of invalid) assert.throws(() => decodeAvatarSignal(value, CONTEXT));
});

test("queued and terminal avatar states are silent while expired speaking may retain expression", () => {
  const silentMouth = { open: 0, energy: 0, viseme: "sil", speaking: false };
  for (const voiceState of ["queued", "delivered", "failed", "unknown"]) {
    const signal = avatarSignal({
      payload: { voice_state: voiceState, expires_in_ms: 0, mouth: silentMouth, expression: null },
    });
    assert.deepEqual(decodeAvatarSignal(signal, CONTEXT), signal);
    assert.throws(() => decodeAvatarSignal(avatarSignal({
      payload: { voice_state: voiceState, expires_in_ms: 1, mouth: silentMouth, expression: null },
    }), CONTEXT));
  }

  const expiredSpeaking = avatarSignal({
    payload: { expires_in_ms: 0, mouth: silentMouth },
  });
  assert.deepEqual(decodeAvatarSignal(expiredSpeaking, CONTEXT), expiredSpeaking);
  const freshSilentSpeaking = avatarSignal({
    payload: { expires_in_ms: 120, mouth: silentMouth },
  });
  assert.deepEqual(decodeAvatarSignal(freshSilentSpeaking, CONTEXT), freshSilentSpeaking);
  assert.throws(() => decodeAvatarSignal(avatarSignal({ payload: { expires_in_ms: 0 } }), CONTEXT));
});
