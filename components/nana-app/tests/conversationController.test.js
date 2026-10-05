import assert from "node:assert/strict";
import test from "node:test";

import { createConversationController } from "../src/conversationController.js";

const EPOCH = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const SESSION = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const TURN = "11111111-1111-4111-8111-111111111111";
const CORRELATION = "22222222-2222-4222-8222-222222222222";
const CONTEXT = { serverEpoch: EPOCH, sessionId: SESSION, clientInstanceId: "cccccccc-cccc-4ccc-8ccc-cccccccccccc" };

function event(type, revision, payload, sequence = revision) {
  return {
    protocol: "nana.private-web-chat.v1",
    server_epoch: EPOCH,
    session_id: SESSION,
    turn_id: TURN,
    correlation_id: CORRELATION,
    revision,
    event_sequence: sequence,
    type,
    payload,
  };
}

test("indexed deltas require next index and final is authoritative", () => {
  const actions = [];
  const controller = createConversationController({ context: CONTEXT, onAction: (action) => actions.push(action) });
  controller.apply(event("turn.state", 1, { state: "accepted" }));
  controller.apply(event("assistant.delta", 2, { delta_index: 1, text: "Chao " }));
  assert.equal(controller.apply(event("assistant.delta", 3, { delta_index: 3, text: "ong" })).action, "reconcile");
  controller.apply(event("assistant.final", 4, { text: "Chao ong" }));
  assert.equal(controller.snapshot().activeTurn.text, "Chao ong");
  assert.ok(actions.some((action) => action.action === "reconcile"));
});

test("voice failure does not erase completed text", () => {
  const controller = createConversationController({ context: CONTEXT });
  controller.apply(event("assistant.final", 1, { text: "Nana da tra loi" }));
  controller.apply(event("turn.state", 2, { state: "complete" }));
  controller.apply(event("voice.state", 3, { state: "failed", reason_code: "voice_failed" }));
  const snapshot = controller.snapshot();
  assert.equal(snapshot.activeTurn.text, "Nana da tra loi");
  assert.equal(snapshot.voiceState, "failed");
});

test('matching protocol name cannot bypass event validation', () => {
  const controller = createConversationController({context: CONTEXT});
  assert.equal(controller.apply(event('assistant.final', 1, {text: 'fixture', private_data: 'bad'})).action, 'protocol_error');
  assert.equal(controller.snapshot().activeTurn, null);
});

test('next turn starts at revision one and cannot inherit the previous text or voice', () => {
  const controller = createConversationController({context: CONTEXT});
  controller.apply(event('assistant.final', 1, {text: 'old'}, 2));
  controller.apply(event('turn.state', 2, {state: 'complete'}, 3));
  const nextTurn = '33333333-3333-4333-8333-333333333333';
  controller.beginTurn({turnId: nextTurn, correlationId: CORRELATION});
  assert.equal(controller.apply({...event('turn.state', 1, {state: 'accepted'}, 4), turn_id: nextTurn}).action, 'applied');
  assert.equal(controller.snapshot().activeTurn.text, '');
  assert.equal(controller.snapshot().voiceState, 'idle');
});

test("old epoch event becomes unknown and never replaces current turn", () => {
  const controller = createConversationController({ context: CONTEXT });
  const result = controller.apply({ ...event("turn.state", 1, { state: "accepted" }), server_epoch: "dddddddd-dddd-4ddd-8ddd-dddddddddddd" });
  assert.equal(result.action, "epoch_changed");
  assert.equal(controller.snapshot().connectionState, "unknown");
});
