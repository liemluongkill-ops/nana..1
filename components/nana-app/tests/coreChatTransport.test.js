import assert from "node:assert/strict";
import test from "node:test";

import { PROTOCOL_NAME, CAPABILITIES, LIMITS } from "../src/coreChatProtocol.js";
import { createCoreChatTransport } from "../src/coreChatTransport.js";

const EPOCH = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const SESSION = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const NEXT_SESSION = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
const CLIENT = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const TURN = "11111111-1111-4111-8111-111111111111";
const CORRELATION = "22222222-2222-4222-8222-222222222222";
const PLAYBACK = "33333333-3333-4333-8333-333333333333";

function harness() {
  const sent = [];
  const sockets = [];
  const timers = [];
  const events = [];
  const visuals = [];
  class FakeWebSocket {
    constructor(url) { this.url = url; this.readyState = 0; sockets.push(this); }
    send(raw) { sent.push(JSON.parse(raw)); }
    close(code = 1000) { this.readyState = 3; this.onclose?.({ code }); }
    open() { this.readyState = 1; this.onopen?.(); }
    receive(value) { this.onmessage?.({ data: JSON.stringify(value) }); }
    disconnect(code = 1006) { this.readyState = 3; this.onclose?.({ code }); }
  }
  const fetchImpl = async () => ({ ok: true, json: async () => ({
    protocol: PROTOCOL_NAME,
    server_epoch: EPOCH,
    handshake_id: '3'.repeat(64),
    capability: "capability-only-memory",
    expires_in_ms: 10000,
  }) });
  const transport = createCoreChatTransport({
    fetchImpl,
    WebSocketImpl: FakeWebSocket,
    clientInstanceId: CLIENT,
    scheduler: (callback, delay) => { timers.push({ callback, delay }); return timers.length; },
    cancelScheduler: () => {},
    randomUuid: (() => { let n = 0; return () => `00000000-0000-4000-8000-${String(++n).padStart(12, "0")}`; })(),
    onEvent: (event) => events.push(event),
    onVisual: (event) => visuals.push(event),
  });
  return { transport, sent, sockets, timers, events, visuals };
}

function avatarSignal(overrides = {}) {
  const base = {
    protocol: PROTOCOL_NAME, type: "avatar.signal", server_epoch: EPOCH, session_id: SESSION,
    turn_id: TURN, correlation_id: CORRELATION, visual_sequence: 1,
    payload: {playback_id: PLAYBACK, voice_state: "speaking", expires_in_ms: 160,
      mouth: {open: 0.5, energy: 0, viseme: "aa", speaking: true}, expression: null},
  };
  return {...base, ...overrides, payload: {...base.payload, ...(overrides.payload || {})}};
}

function confirm(h, sessionId = SESSION) {
  const ws = h.sockets.at(-1);
  ws.open();
  ws.receive({ type: "session.welcome", protocol: PROTOCOL_NAME, server_epoch: EPOCH, provisional_session_id: sessionId, handshake_nonce: h.sent.findLast(f => f.type === 'session.hello').handshake_nonce, limits: LIMITS, capabilities: CAPABILITIES });
  ws.receive({ type: "session.active", protocol: PROTOCOL_NAME, server_epoch: EPOCH, session_id: sessionId, event_sequence: 1 });
  ws.receive({ type: "session.confirmed", protocol: PROTOCOL_NAME, server_epoch: EPOCH, session_id: sessionId });
  return ws;
}

function coreEvent({turnId = TURN, correlationId = CORRELATION, sessionId = SESSION,
  revision, sequence, type = "turn.state", payload}) {
  return {protocol: PROTOCOL_NAME, server_epoch: EPOCH, session_id: sessionId,
    turn_id: turnId, correlation_id: correlationId, revision,
    event_sequence: sequence, type, payload};
}

test("bootstraps, confirms four frame session and clears capability from memory", async () => {
  const h = harness();
  await h.transport.start();
  const ws = h.sockets[0];
  ws.open();
  assert.equal(h.sent[0].type, "session.hello");
  assert.equal(h.sent[0].capability, "capability-only-memory");
  ws.receive({ type: "session.welcome", protocol: PROTOCOL_NAME, server_epoch: EPOCH, provisional_session_id: SESSION, handshake_nonce: h.sent[0].handshake_nonce, limits: LIMITS, capabilities: CAPABILITIES });
  assert.equal(h.sent[1].type, "session.ready");
  ws.receive({ type: "session.active", protocol: PROTOCOL_NAME, server_epoch: EPOCH, session_id: SESSION, event_sequence: 1 });
  assert.equal(h.sent[2].type, "session.active_ack");
  ws.receive({ type: "session.confirmed", protocol: PROTOCOL_NAME, server_epoch: EPOCH, session_id: SESSION });
  assert.equal(h.transport.snapshot().status, "connected");
  assert.equal(h.transport.snapshot().capabilityPresent, false);
});

test("disconnect after accepted reconciles and never resubmits", async () => {
  const h = harness();
  await h.transport.start();
  const ws = confirm(h);
  h.transport.submit({ turnId: TURN, correlationId: CORRELATION, text: "hello" });
  ws.receive(coreEvent({revision: 1, sequence: 2, payload: {state: "accepted"}}));
  ws.disconnect();
  assert.equal(h.sent.filter((frame) => frame.type === "chat.submit").length, 1);
  await h.transport.start();
  confirm(h);
  assert.equal(h.sent.at(-1).type, "chat.reconcile");
});

test("session ping receives pong and stop closes socket without replay", async () => {
  const h = harness();
  await h.transport.start();
  const ws = confirm(h);
  ws.receive({ type: "session.ping", protocol: PROTOCOL_NAME, server_epoch: EPOCH, session_id: SESSION });
  assert.equal(h.sent.at(-1).type, "session.pong");
  h.transport.stop();
  assert.equal(h.transport.snapshot().status, "stopped");
});

test('retry before confirmation uses same capability and nonce exactly once', async () => {
  const h = harness(); await h.transport.start();
  let ws = h.sockets.at(-1); ws.open();
  const hello = h.sent.at(-1);
  ws.disconnect(1006);
  await h.transport.start(); ws = h.sockets.at(-1); ws.open();
  assert.deepEqual(h.sent.at(-1), hello);
  ws.receive({type: 'session.confirmed', protocol: PROTOCOL_NAME, server_epoch: EPOCH, session_id: SESSION});
  assert.notEqual(h.transport.snapshot().status, 'connected');
  assert.equal(h.transport.snapshot().capabilityPresent, false);
});

test('public snapshots never contain text and stop forgets the turn', async () => {
  const h = harness(); await h.transport.start(); confirm(h);
  h.transport.submit({turnId: TURN, correlationId: CORRELATION, text: 'PRIVATE_SENTINEL'});
  assert.ok(!JSON.stringify(h.transport.snapshot()).includes('PRIVATE_SENTINEL'));
  assert.throws(() => h.transport.submit({turnId: TURN, correlationId: CORRELATION, text: 'again'}));
  h.transport.stop(); assert.equal(h.transport.snapshot().pendingTurn, null);
});

test('stop then start does not reuse an unresolved old bootstrap', async () => {
  const requests = [];
  const transport = createCoreChatTransport({clientInstanceId: CLIENT,
    fetchImpl: () => new Promise(resolve => requests.push(resolve)),
    scheduler: () => 1, cancelScheduler: () => {},
    WebSocketImpl: class {close() {}},
  });
  const old = transport.start(); transport.stop();
  const next = transport.start();
  assert.equal(requests.length, 2);
  for (const resolve of requests) resolve({ok: false});
  await Promise.all([old, next]); transport.stop();
});

test("routes avatar signals separately without synthesizing chat voice or ordering state", async () => {
  const h = harness();
  await h.transport.start();
  const ws = confirm(h);
  h.transport.submit({turnId: TURN, correlationId: CORRELATION, text: "hello"});

  ws.receive(avatarSignal());

  assert.equal(h.visuals.length, 1);
  assert.equal(h.events.length, 0, "avatar frames never enter the regular event callback");
  assert.equal(h.transport.snapshot().lastTurn.revision, 0);
  assert.equal(h.transport.snapshot().lastTurn.voiceState, "idle");
  assert.equal(h.transport.snapshot().pendingTurn.voiceState, "idle");
});

test("orders sequence globally across unrelated turns and ignores stale same-session events", async () => {
  const h = harness();
  await h.transport.start();
  const ws = confirm(h);
  h.transport.submit({turnId: TURN, correlationId: CORRELATION, text: "hello"});

  ws.receive(coreEvent({revision: 1, sequence: 2, payload: {state: "accepted"}}));
  ws.receive(coreEvent({
    turnId: "44444444-4444-4444-8444-444444444444",
    revision: 1,
    sequence: 3,
    type: "error",
    payload: {code: "unsupported_in_v1", retryable: false},
  }));
  ws.receive(coreEvent({revision: 2, sequence: 4, payload: {state: "thinking"}}));
  ws.receive(coreEvent({revision: 3, sequence: 3, payload: {state: "generated"}}));
  ws.receive(coreEvent({revision: 3, sequence: 5, payload: {state: "complete"}}));

  assert.deepEqual(h.events.map((event) => event.event_sequence), [2, 4, 5]);
  assert.equal(h.transport.snapshot().lastTurn.revision, 3);
  assert.equal(h.transport.snapshot().lastTurn.state, "complete");
});

test("a replacement session resets wire ordering without resetting retained turn revision", async () => {
  const h = harness();
  await h.transport.start();
  const first = confirm(h);
  h.transport.submit({turnId: TURN, correlationId: CORRELATION, text: "hello"});
  first.receive(coreEvent({revision: 1, sequence: 2, payload: {state: "accepted"}}));
  first.receive(coreEvent({revision: 2, sequence: 3, payload: {state: "thinking"}}));
  first.disconnect();

  await h.transport.start();
  const replacement = confirm(h, NEXT_SESSION);
  assert.equal(h.sent.at(-1).type, "chat.reconcile");
  assert.equal(h.sent.at(-1).revision, 2);
  replacement.receive(coreEvent({
    sessionId: NEXT_SESSION,
    revision: 3,
    sequence: 2,
    type: "turn.snapshot",
    payload: {turn_server_epoch: EPOCH, turn_state: "complete", voice_state: "delivered",
      text: "fixture final", delta_index: 0, reason_code: null},
  }));

  assert.equal(h.events.at(-1).session_id, NEXT_SESSION);
  assert.equal(h.events.at(-1).event_sequence, 2);
  assert.equal(h.transport.snapshot().lastTurn.revision, 3);
  assert.equal(h.transport.snapshot().lastTurn.voiceState, "delivered");
});

test("malformed avatar signals are dropped without failing the authenticated chat", async () => {
  const h = harness();
  await h.transport.start();
  const ws = confirm(h);

  ws.receive(avatarSignal({revision: 1}));

  assert.equal(h.visuals.length, 0);
  assert.equal(ws.readyState, 1);
  assert.equal(h.transport.snapshot().status, "connected");
});
