import assert from "node:assert/strict";
import test from "node:test";

import {
  PUBLIC_SIGNAL_ENDPOINT,
  PUBLIC_SIGNAL_PROTOCOL,
  UNITY_RUNTIME_OBJECT,
  bindObsSignalLifecycle,
  createObsSignalConsumer,
} from "../src/obsSignals.js";

function currentSignal(overrides = {}) {
  return {
    stream_session_id: "stream-1",
    playback_id: "playback-1",
    attempt_id: "attempt-1",
    event_id: "event-1",
    sequence: 1,
    open: 0.6,
    energy: 0.4,
    viseme: "aa",
    speaking: true,
    age_ms: 20,
    expires_in_ms: 160,
    expression: null,
    terminal_reason: null,
    ...overrides,
  };
}

function envelope(current, overrides = {}) {
  return {
    ok: true,
    protocol: PUBLIC_SIGNAL_PROTOCOL,
    cursor: 1,
    stale_after_ms: 180,
    current,
    ...overrides,
  };
}

function jsonResponse(body, { ok = true, status = 200 } = {}) {
  return {
    ok,
    status,
    async json() {
      return body;
    },
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
    clear(id) {
      tasks.delete(id);
    },
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
    pendingDelays() {
      return [...tasks.values()].map(({ delay }) => delay).sort((a, b) => a - b);
    },
  };
}

function consumerHarness(responses = []) {
  const calls = [];
  const requests = [];
  const timers = manualTimers();
  const queue = [...responses];
  const consumer = createObsSignalConsumer({
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      const next = queue.shift();
      if (next instanceof Error) throw next;
      assert.ok(next, "expected a queued fetch response");
      return next;
    },
    sendUnity: (objectName, method, payload) => {
      calls.push({ objectName, method, payload: JSON.parse(payload) });
      return true;
    },
    setTimer: timers.set,
    clearTimer: timers.clear,
  });
  return { calls, consumer, requests, timers };
}

function mouthCalls(calls) {
  return calls.filter(({ method }) => method === "SetMouthVisemeJson");
}

function expressionCalls(calls) {
  return calls.filter(({ method }) => method === "ReceiveAvatarIntentJson");
}

test("a fresh public signal drives the confirmed Unity mouth and expression methods", async () => {
  const signal = currentSignal({
    expression: {
      event_id: "expression-1",
      action: "happy",
      expires_in_ms: 1200,
    },
  });
  const harness = consumerHarness([jsonResponse(envelope(signal))]);

  assert.equal(await harness.consumer.pollOnce(), true);

  assert.deepEqual(harness.calls, [
    {
      objectName: UNITY_RUNTIME_OBJECT,
      method: "SetMouthVisemeJson",
      payload: { open: 0.6, energy: 0.4, viseme: "aa", speaking: true },
    },
    {
      objectName: UNITY_RUNTIME_OBJECT,
      method: "ReceiveAvatarIntentJson",
      payload: { intent_id: "expression-1", action: "happy", duration_s: 1.2 },
    },
  ]);
  assert.deepEqual(harness.timers.pendingDelays(), [160]);
});

test("the consumer uses only the fixed loopback endpoint and an integer cursor", async () => {
  const harness = consumerHarness([
    jsonResponse(envelope(currentSignal(), { cursor: 7 })),
    jsonResponse(envelope(null, { cursor: 7 })),
  ]);

  await harness.consumer.pollOnce();
  await harness.consumer.pollOnce();

  assert.equal(PUBLIC_SIGNAL_ENDPOINT, "http://127.0.0.1:8766/v1/avatar/public-signals");
  assert.deepEqual(harness.requests.map(({ url }) => url), [
    `${PUBLIC_SIGNAL_ENDPOINT}?after=0`,
    `${PUBLIC_SIGNAL_ENDPOINT}?after=7`,
  ]);
  for (const { options } of harness.requests) {
    assert.equal(options.method, "GET");
    assert.equal(options.credentials, "omit");
    assert.equal(options.cache, "no-store");
  }
});

test("all approved visemes and expressions pass while excluded actions do not", async () => {
  const visemes = ["sil", "aa", "ih", "ee", "oh", "ou", "nn"];
  const actions = [
    "curious", "happy", "nod", "listen", "think", "surprised", "shy", "playful",
    "wink_soft_smile", "surprised_pout", "serious_think", "cat_teary_smile",
    "shy_crying", "playful_wink", "settle", "idle", "blink",
  ];
  const responses = actions.map((action, index) => jsonResponse(envelope(currentSignal({
    playback_id: `playback-${index + 1}`,
    attempt_id: `attempt-${index + 1}`,
    event_id: `event-${index + 1}`,
    sequence: 1,
    viseme: visemes[index % visemes.length],
    expression: { event_id: `expression-${index + 1}`, action, expires_in_ms: 500 },
  }), { cursor: index + 1 })));
  responses.push(jsonResponse(envelope(currentSignal({
    playback_id: "playback-blocked",
    attempt_id: "attempt-blocked",
    event_id: "event-blocked",
    expression: { event_id: "expression-blocked", action: "wave", expires_in_ms: 500 },
  }), { cursor: actions.length + 1 })));
  const harness = consumerHarness(responses);

  for (const _ of actions) assert.equal(await harness.consumer.pollOnce(), true);
  assert.equal(await harness.consumer.pollOnce(), false);

  assert.deepEqual(expressionCalls(harness.calls).map(({ payload }) => payload.action), actions);
  assert.equal(expressionCalls(harness.calls).some(({ payload }) => payload.action === "wave"), false);
  assert.equal(mouthCalls(harness.calls).at(-1).payload.viseme, "sil");
});

test("exact schemas reject private payloads, malformed IDs, numbers, and protocol fields", async () => {
  const invalidBodies = [
    { ...envelope(currentSignal()), history: [] },
    envelope({ ...currentSignal(), text: "private" }),
    envelope({ ...currentSignal(), pcm: "AAAA" }),
    envelope(currentSignal({ stream_session_id: " stream-1" })),
    envelope(currentSignal({ event_id: "event\u001fprivate" })),
    envelope(currentSignal({ event_id: "event\u0085private" })),
    envelope(currentSignal({ playback_id: "x".repeat(257) })),
    envelope(currentSignal({ sequence: 0 })),
    envelope(currentSignal({ open: Number.NaN })),
    envelope(currentSignal({ energy: 1.01 })),
    envelope(currentSignal({ speaking: "true" })),
    envelope(currentSignal({ age_ms: 181 })),
    envelope(currentSignal({ expires_in_ms: 181 })),
    envelope(currentSignal({ age_ms: 100, expires_in_ms: 100 })),
    envelope(currentSignal({ viseme: "A" })),
    { ...envelope(currentSignal()), ok: false },
    { ...envelope(currentSignal()), protocol: "nana.avatar.mouth.v1" },
    { ...envelope(currentSignal()), stale_after_ms: 181 },
    { ...envelope(currentSignal()), cursor: 1.5 },
  ];
  const harness = consumerHarness(invalidBodies.map((body) => jsonResponse(body)));

  for (const _ of invalidBodies) assert.equal(await harness.consumer.pollOnce(), false);

  assert.equal(mouthCalls(harness.calls).every(({ payload }) => (
    payload.open === 0 && payload.energy === 0 && payload.viseme === "sil" && payload.speaking === false
  )), true);
  assert.equal(expressionCalls(harness.calls).length, 0);
});

test("duplicate and backward cursors or per-playback sequences fail closed", async () => {
  const harness = consumerHarness([
    jsonResponse(envelope(currentSignal({ sequence: 4 }), { cursor: 8 })),
    jsonResponse(envelope(currentSignal({ sequence: 5 }), { cursor: 8 })),
    jsonResponse(envelope(currentSignal({ sequence: 3 }), { cursor: 9 })),
    jsonResponse(envelope(currentSignal({ sequence: 4 }), { cursor: 10 })),
  ]);

  assert.equal(await harness.consumer.pollOnce(), true);
  assert.equal(await harness.consumer.pollOnce(), false);
  assert.equal(await harness.consumer.pollOnce(), false);
  assert.equal(await harness.consumer.pollOnce(), false);

  assert.equal(mouthCalls(harness.calls).filter(({ payload }) => payload.open > 0).length, 1);
  assert.deepEqual(mouthCalls(harness.calls).at(-1).payload, {
    open: 0,
    energy: 0,
    viseme: "sil",
    speaking: false,
  });
});

test("fresh playback and session transitions retire old identities against replay", async () => {
  const first = currentSignal({ sequence: 5 });
  const nextPlayback = currentSignal({
    playback_id: "playback-2",
    attempt_id: "attempt-2",
    event_id: "event-2",
  });
  const nextSession = currentSignal({
    stream_session_id: "stream-2",
    playback_id: "playback-3",
    attempt_id: "attempt-3",
    event_id: "event-3",
  });
  const harness = consumerHarness([
    jsonResponse(envelope(first, { cursor: 1 })),
    jsonResponse(envelope(nextPlayback, { cursor: 2 })),
    jsonResponse(envelope({ ...first, sequence: 6 }, { cursor: 3 })),
    jsonResponse(envelope(nextSession, { cursor: 4 })),
    jsonResponse(envelope({ ...nextPlayback, sequence: 2 }, { cursor: 5 })),
  ]);

  assert.equal(await harness.consumer.pollOnce(), true);
  assert.equal(await harness.consumer.pollOnce(), true);
  assert.equal(await harness.consumer.pollOnce(), false);
  assert.equal(await harness.consumer.pollOnce(), true);
  assert.equal(await harness.consumer.pollOnce(), false);

  assert.equal(mouthCalls(harness.calls).filter(({ payload }) => payload.open > 0).length, 3);
});

test("terminal state is validated, closes immediately, and cannot be replayed", async () => {
  const terminal = currentSignal({
    sequence: 2,
    open: 0,
    energy: 0,
    viseme: "sil",
    speaking: false,
    expression: null,
    terminal_reason: "completed",
    age_ms: 0,
    expires_in_ms: 180,
  });
  const invalidTerminal = { ...terminal, sequence: 3, terminal_reason: "unknown" };
  const harness = consumerHarness([
    jsonResponse(envelope(currentSignal(), { cursor: 1 })),
    jsonResponse(envelope(terminal, { cursor: 2 })),
    jsonResponse(envelope({ ...terminal, sequence: 3 }, { cursor: 3 })),
    jsonResponse(envelope(invalidTerminal, { cursor: 4 })),
  ]);

  assert.equal(await harness.consumer.pollOnce(), true);
  assert.equal(await harness.consumer.pollOnce(), true);
  assert.equal(await harness.consumer.pollOnce(), false);
  assert.equal(await harness.consumer.pollOnce(), false);

  assert.deepEqual(mouthCalls(harness.calls).at(-1).payload, {
    open: 0,
    energy: 0,
    viseme: "sil",
    speaking: false,
  });
});

test("unchanged null state preserves a fresh sample only until its local expiry", async () => {
  const harness = consumerHarness([
    jsonResponse(envelope(currentSignal({ expires_in_ms: 90, age_ms: 30 }), { cursor: 4 })),
    jsonResponse(envelope(null, { cursor: 4 })),
  ]);

  assert.equal(await harness.consumer.pollOnce(), true);
  assert.equal(await harness.consumer.pollOnce(), true);
  assert.equal(mouthCalls(harness.calls).length, 1);

  assert.equal(harness.timers.runNext(), 90);
  assert.deepEqual(mouthCalls(harness.calls).at(-1).payload, {
    open: 0,
    energy: 0,
    viseme: "sil",
    speaking: false,
  });
});

test("a newer null cursor closes removed current state immediately", async () => {
  const harness = consumerHarness([
    jsonResponse(envelope(currentSignal(), { cursor: 3 })),
    jsonResponse(envelope(null, { cursor: 4 })),
  ]);

  assert.equal(await harness.consumer.pollOnce(), true);
  assert.equal(await harness.consumer.pollOnce(), true);

  assert.deepEqual(mouthCalls(harness.calls).at(-1).payload, {
    open: 0,
    energy: 0,
    viseme: "sil",
    speaking: false,
  });
});

test("an expression event is emitted once while newer mouth samples continue", async () => {
  const expression = { event_id: "expression-1", action: "nod", expires_in_ms: 700 };
  const harness = consumerHarness([
    jsonResponse(envelope(currentSignal({ expression }), { cursor: 1 })),
    jsonResponse(envelope(currentSignal({ sequence: 2, expression }), { cursor: 2 })),
    jsonResponse(envelope(currentSignal({
      sequence: 3,
      expression: { ...expression, event_id: "expression-expired", expires_in_ms: 0 },
    }), { cursor: 3 })),
  ]);

  assert.equal(await harness.consumer.pollOnce(), true);
  assert.equal(await harness.consumer.pollOnce(), true);
  assert.equal(await harness.consumer.pollOnce(), true);

  assert.equal(mouthCalls(harness.calls).filter(({ payload }) => payload.open > 0).length, 3);
  assert.deepEqual(expressionCalls(harness.calls).map(({ payload }) => payload.intent_id), ["expression-1"]);
});

test("an expired expression identity cannot be revived by a later signal", async () => {
  const expired = { event_id: "expression-expired", action: "nod", expires_in_ms: 0 };
  const harness = consumerHarness([
    jsonResponse(envelope(currentSignal({ expression: expired }), { cursor: 1 })),
    jsonResponse(envelope(currentSignal({
      sequence: 2,
      expression: { ...expired, expires_in_ms: 700 },
    }), { cursor: 2 })),
  ]);

  assert.equal(await harness.consumer.pollOnce(), true);
  assert.equal(await harness.consumer.pollOnce(), true);
  assert.equal(expressionCalls(harness.calls).length, 0);
});

test("replay tracking fails closed at capacity instead of evicting retired playback IDs", async () => {
  const responses = [];
  for (let index = 1; index <= 258; index += 1) {
    responses.push(jsonResponse(envelope(currentSignal({
      playback_id: `playback-${index}`,
      attempt_id: `attempt-${index}`,
      event_id: `event-${index}`,
    }), { cursor: index })));
  }
  responses.push(jsonResponse(envelope(currentSignal({
    playback_id: "playback-1",
    attempt_id: "attempt-1",
    event_id: "event-1",
    sequence: 2,
  }), { cursor: 259 })));
  const harness = consumerHarness(responses);

  for (let index = 1; index <= 257; index += 1) {
    assert.equal(await harness.consumer.pollOnce(), true);
  }
  assert.equal(await harness.consumer.pollOnce(), false);
  assert.equal(await harness.consumer.pollOnce(), false);
  assert.deepEqual(mouthCalls(harness.calls).at(-1).payload, {
    open: 0,
    energy: 0,
    viseme: "sil",
    speaking: false,
  });
});

test("fetch failure closes the mouth and reconnect accepts only fresher current state", async () => {
  const harness = consumerHarness([
    jsonResponse(envelope(currentSignal({ sequence: 5 }), { cursor: 10 })),
    new Error("connection refused"),
    jsonResponse(envelope(currentSignal({ sequence: 6 }), { cursor: 1 })),
    new Error("connection reset"),
    jsonResponse(envelope(currentSignal({ sequence: 5 }), { cursor: 1 })),
  ]);

  assert.equal(await harness.consumer.pollOnce(), true);
  assert.equal(await harness.consumer.pollOnce(), false);
  assert.equal(await harness.consumer.pollOnce(), true);
  assert.equal(await harness.consumer.pollOnce(), false);
  assert.equal(await harness.consumer.pollOnce(), false);

  assert.deepEqual(harness.requests.map(({ url }) => url), [
    `${PUBLIC_SIGNAL_ENDPOINT}?after=0`,
    `${PUBLIC_SIGNAL_ENDPOINT}?after=10`,
    `${PUBLIC_SIGNAL_ENDPOINT}?after=0`,
    `${PUBLIC_SIGNAL_ENDPOINT}?after=1`,
    `${PUBLIC_SIGNAL_ENDPOINT}?after=0`,
  ]);
  assert.equal(mouthCalls(harness.calls).filter(({ payload }) => payload.open > 0).length, 2);
  assert.deepEqual(mouthCalls(harness.calls).at(-1).payload, {
    open: 0,
    energy: 0,
    viseme: "sil",
    speaking: false,
  });
});

test("a restarted Core cursor epoch accepts fresh state but still rejects old sequence replay", async () => {
  const harness = consumerHarness([
    jsonResponse(envelope(currentSignal({ sequence: 5 }), { cursor: 10 })),
    jsonResponse(envelope(currentSignal({ sequence: 6 }), { cursor: 1 })),
    jsonResponse(envelope(currentSignal({ sequence: 5 }), { cursor: 2 })),
  ]);

  assert.equal(await harness.consumer.pollOnce(), true);
  assert.equal(await harness.consumer.pollOnce(), true);
  assert.equal(await harness.consumer.pollOnce(), false);
  assert.deepEqual(harness.requests.map(({ url }) => url), [
    `${PUBLIC_SIGNAL_ENDPOINT}?after=0`,
    `${PUBLIC_SIGNAL_ENDPOINT}?after=10`,
    `${PUBLIC_SIGNAL_ENDPOINT}?after=1`,
  ]);
  assert.equal(mouthCalls(harness.calls).filter(({ payload }) => payload.open > 0).length, 2);
});

test("HTTP, JSON, and stop teardown paths are silent and never acknowledge delivery", async () => {
  const harness = consumerHarness([
    jsonResponse(envelope(currentSignal()), { ok: false, status: 503 }),
    {
      ok: true,
      status: 200,
      async json() {
        throw new SyntaxError("bad json");
      },
    },
    jsonResponse(envelope(currentSignal(), { cursor: 2 })),
  ]);

  assert.equal(await harness.consumer.pollOnce(), false);
  assert.equal(await harness.consumer.pollOnce(), false);
  assert.equal(await harness.consumer.pollOnce(), true);
  harness.consumer.stop();

  assert.deepEqual(mouthCalls(harness.calls).at(-1).payload, {
    open: 0,
    energy: 0,
    viseme: "sil",
    speaking: false,
  });
  assert.equal(harness.calls.some(({ method }) => /ack|receipt|deliver/i.test(method)), false);
});

test("visibility and page teardown stop with silence while a visible source resumes once", () => {
  class FakeEventTarget {
    constructor() {
      this.listeners = new Map();
    }

    addEventListener(type, listener) {
      if (!this.listeners.has(type)) this.listeners.set(type, new Set());
      this.listeners.get(type).add(listener);
    }

    removeEventListener(type, listener) {
      this.listeners.get(type)?.delete(listener);
    }

    dispatch(type) {
      for (const listener of this.listeners.get(type) || []) listener();
    }
  }

  const documentTarget = new FakeEventTarget();
  const windowTarget = new FakeEventTarget();
  const lifecycle = [];
  const consumer = {
    start() {
      lifecycle.push("start");
    },
    stop() {
      lifecycle.push("stop");
    },
  };
  documentTarget.visibilityState = "visible";

  const dispose = bindObsSignalLifecycle({ consumer, documentTarget, windowTarget });
  documentTarget.visibilityState = "hidden";
  documentTarget.dispatch("visibilitychange");
  documentTarget.visibilityState = "visible";
  documentTarget.dispatch("visibilitychange");
  windowTarget.dispatch("pagehide");
  documentTarget.dispatch("visibilitychange");
  dispose();

  assert.deepEqual(lifecycle, ["start", "stop", "start", "stop"]);
});

test("a stopped polling generation cannot apply or reschedule after a rapid restart", async () => {
  let resolveFetch;
  const calls = [];
  const timers = manualTimers();
  const consumer = createObsSignalConsumer({
    fetchImpl: () => new Promise((resolve) => {
      resolveFetch = resolve;
    }),
    sendUnity: (objectName, method, payload) => {
      calls.push({ objectName, method, payload: JSON.parse(payload) });
      return true;
    },
    setTimer: timers.set,
    clearTimer: timers.clear,
  });

  consumer.start();
  consumer.stop();
  consumer.start();
  await Promise.resolve();
  resolveFetch(jsonResponse(envelope(currentSignal())));
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();

  assert.equal(mouthCalls(calls).some(({ payload }) => payload.open > 0), false);
  assert.deepEqual(timers.pendingDelays(), [80]);
});

test("a throwing Unity bridge fails closed without breaking poll or stop", async () => {
  const timers = manualTimers();
  const consumer = createObsSignalConsumer({
    fetchImpl: async () => jsonResponse(envelope(currentSignal())),
    sendUnity() {
      throw new Error("Unity runtime disposed");
    },
    setTimer: timers.set,
    clearTimer: timers.clear,
  });

  assert.equal(await consumer.pollOnce(), false);
  assert.doesNotThrow(() => consumer.stop());
});
