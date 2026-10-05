import assert from "node:assert/strict";
import test from "node:test";

import { PROTOCOL_NAME } from "../src/coreChatProtocol.js";
import {
  UNITY_RUNTIME_OBJECT,
  createPrivateAvatarSignalConsumer,
} from "../src/privateAvatarSignals.js";

const EPOCH = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const SESSION = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const NEXT_SESSION = "abababab-abab-4bab-8bab-abababababab";
const TURN = "11111111-1111-4111-8111-111111111111";
const CORRELATION = "22222222-2222-4222-8222-222222222222";
const PLAYBACK = "33333333-3333-4333-8333-333333333333";
const EXPRESSION = "44444444-4444-4444-8444-444444444444";
const NEXT_PLAYBACK = "55555555-5555-4555-8555-555555555555";
const NEXT_TURN = "66666666-6666-4666-8666-666666666666";
const NEXT_CORRELATION = "77777777-7777-4777-8777-777777777777";
const SETTLE = "99999999-9999-4999-8999-999999999999";

function signal(overrides = {}) {
  const base = {
    protocol: PROTOCOL_NAME,
    type: "avatar.signal",
    server_epoch: EPOCH,
    session_id: SESSION,
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

function manualTimers() {
  let nextId = 1;
  const tasks = new Map();
  return {
    set(callback, delay) {
      const id = nextId++;
      tasks.set(id, { callback, delay });
      return id;
    },
    clear(id) { tasks.delete(id); },
    runNext() {
      const entry = [...tasks.entries()].sort((left, right) => (
        left[1].delay - right[1].delay || left[0] - right[0]
      ))[0];
      assert.ok(entry, "expected a pending timer");
      const [id, task] = entry;
      tasks.delete(id);
      task.callback();
      return task.delay;
    },
    delays() { return [...tasks.values()].map(({ delay }) => delay).sort((a, b) => a - b); },
  };
}

function harness() {
  const calls = [];
  const timers = manualTimers();
  const consumer = createPrivateAvatarSignalConsumer({
    sendUnity(objectName, method, payload) {
      calls.push({ objectName, method, payload: JSON.parse(payload) });
      return true;
    },
    setTimer: timers.set,
    clearTimer: timers.clear,
    randomUuid: () => SETTLE,
  });
  consumer.syncContext({
    connected: true,
    serverEpoch: EPOCH,
    sessionId: SESSION,
    turnId: TURN,
    correlationId: CORRELATION,
    voiceState: "queued",
  });
  consumer.setUnityReady(true);
  return { calls, consumer, timers };
}

function mouths(calls) {
  return calls.filter(({ method }) => method === "SetMouthVisemeJson");
}

function expressions(calls) {
  return calls.filter(({ method }) => method === "ReceiveAvatarIntentJson");
}

test("same-turn signals drive mouth and one expression with an expiry watchdog", () => {
  const h = harness();

  assert.equal(h.consumer.accept(signal()), true);
  assert.deepEqual(h.calls, [
    {
      objectName: UNITY_RUNTIME_OBJECT,
      method: "SetMouthVisemeJson",
      payload: { open: 0.6, energy: 0, viseme: "aa", speaking: true },
    },
    {
      objectName: UNITY_RUNTIME_OBJECT,
      method: "ReceiveAvatarIntentJson",
      payload: { intent_id: EXPRESSION, action: "happy", duration_s: 0.9 },
    },
  ]);
  assert.deepEqual(h.timers.delays(), [160, 900]);
  assert.equal(h.timers.runNext(), 160);
  assert.deepEqual(mouths(h.calls).at(-1).payload, {open: 0, energy: 0, viseme: "sil", speaking: false});
  assert.equal(Object.hasOwn(h.consumer.snapshot(), "voiceState"), false);
});

test("wrong identity, epoch, and exact-schema violations close the mouth", () => {
  const invalid = [
    signal({turn_id: NEXT_TURN}),
    signal({correlation_id: NEXT_CORRELATION}),
    signal({server_epoch: "88888888-8888-4888-8888-888888888888"}),
    signal({revision: 1}),
  ];

  for (const value of invalid) {
    const h = harness();
    assert.equal(h.consumer.accept(value), false);
    assert.deepEqual(mouths(h.calls).at(-1).payload, {open: 0, energy: 0, viseme: "sil", speaking: false});
  }
});

test("visual sequence is monotonic per playback and retired playback cannot return", () => {
  const h = harness();
  assert.equal(h.consumer.accept(signal()), true);
  assert.equal(h.consumer.accept(signal()), false);
  assert.equal(h.consumer.accept(signal({
    visual_sequence: 1,
    payload: {playback_id: NEXT_PLAYBACK, expression: null},
  })), true);
  assert.equal(h.consumer.accept(signal({visual_sequence: 2, payload: {expression: null}})), false);
  assert.equal(mouths(h.calls).at(-1).payload.viseme, "sil");
});

test("terminal visual and independent JS voice receipt both prevent resurrection", () => {
  const silent = {open: 0, energy: 0, viseme: "sil", speaking: false};
  const h = harness();
  assert.equal(h.consumer.accept(signal({payload: {expression: null}})), true);
  assert.equal(h.consumer.accept(signal({
    visual_sequence: 2,
    payload: {voice_state: "delivered", expires_in_ms: 0, mouth: silent, expression: null},
  })), true);
  assert.equal(h.consumer.accept(signal({visual_sequence: 3, payload: {expression: null}})), false);

  const missed = harness();
  assert.equal(missed.consumer.accept(signal({payload: {expression: null}})), true);
  missed.consumer.syncContext({
    connected: true,
    serverEpoch: EPOCH,
    sessionId: SESSION,
    turnId: TURN,
    correlationId: CORRELATION,
    voiceState: "failed",
  });
  assert.deepEqual(mouths(missed.calls).at(-1).payload, silent);
  assert.equal(missed.consumer.accept(signal({visual_sequence: 2, payload: {expression: null}})), false);
});

test("expired speaking closes mouth but may still dispatch a bounded expression once", () => {
  const h = harness();
  const silent = {open: 0, energy: 0, viseme: "sil", speaking: false};
  assert.equal(h.consumer.accept(signal({payload: {expires_in_ms: 0, mouth: silent}})), true);
  assert.deepEqual(mouths(h.calls).at(-1).payload, silent);
  assert.equal(expressions(h.calls).length, 1);
  assert.equal(h.consumer.accept(signal({
    visual_sequence: 2,
    payload: {expires_in_ms: 0, mouth: silent},
  })), true);
  assert.equal(expressions(h.calls).length, 1);
  assert.deepEqual(h.timers.delays(), [900]);
});

test("lifecycle cleanup settles only an expression still owned by this consumer", () => {
  const h = harness();
  assert.equal(h.consumer.accept(signal()), true);

  h.consumer.setVisible(false);

  assert.deepEqual(expressions(h.calls).at(-1).payload, {
    intent_id: SETTLE,
    action: "settle",
    duration_s: 0.18,
  });

  const expired = harness();
  assert.equal(expired.consumer.accept(signal()), true);
  assert.equal(expired.timers.runNext(), 160);
  assert.equal(expired.timers.runNext(), 900);
  expired.consumer.setVisible(false);
  assert.equal(expressions(expired.calls).length, 1);
});

test("same-turn reconnect keeps expression dedupe across a new session", () => {
  const h = harness();
  assert.equal(h.consumer.accept(signal()), true);
  h.consumer.syncContext({connected: false});
  h.consumer.syncContext({
    connected: true,
    serverEpoch: EPOCH,
    sessionId: NEXT_SESSION,
    turnId: TURN,
    correlationId: CORRELATION,
    voiceState: "queued",
  });

  assert.equal(h.consumer.accept(signal({
    session_id: NEXT_SESSION,
    visual_sequence: 2,
  })), true);
  assert.deepEqual(expressions(h.calls).map(({payload}) => payload.action), ["happy", "settle"]);
});

test("disconnect, new turn, hidden page, and Unity loss all close live mouth", () => {
  const h = harness();
  assert.equal(h.consumer.accept(signal({payload: {expression: null}})), true);
  h.consumer.syncContext({connected: false});
  assert.equal(mouths(h.calls).at(-1).payload.viseme, "sil");

  h.consumer.syncContext({
    connected: true, serverEpoch: EPOCH, sessionId: SESSION,
    turnId: NEXT_TURN, correlationId: NEXT_CORRELATION, voiceState: "queued",
  });
  assert.equal(h.consumer.accept(signal()), false);
  h.consumer.setVisible(false);
  assert.equal(mouths(h.calls).at(-1).payload.viseme, "sil");
  h.consumer.setUnityReady(false);
  assert.equal(h.consumer.snapshot().unityReady, false);
});
