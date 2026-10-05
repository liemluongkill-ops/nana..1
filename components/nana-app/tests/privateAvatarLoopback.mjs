// Invoked by the guarded Python private-avatar smoke on an ephemeral port.
import assert from 'node:assert/strict';
import {createCoreChatTransport, PRIVATE_WEB_CHAT_BOOTSTRAP_URL, PRIVATE_WEB_CHAT_WS_URL} from '../src/coreChatTransport.js';
import {createConversationController} from '../src/conversationController.js';
const port = Number(process.argv[2]);
assert.ok(Number.isInteger(port) && port > 0 && port !== 8767);
const origin = 'http://127.0.0.1:5174';
const controller = createConversationController();
const visuals = [];
let submits = 0;
class Socket extends WebSocket {
  constructor(url) {
    assert.equal(url, PRIVATE_WEB_CHAT_WS_URL);
    super(url.replace(':8767/', ':' + port + '/'), {headers: {Origin: origin}});
  }
  send(data) {
    if (JSON.parse(data).type === 'chat.submit') submits++;
    super.send(data);
  }
}
const transport = createCoreChatTransport({
  WebSocketImpl: Socket,
  fetchImpl: (url, options) => {
    assert.equal(url, PRIVATE_WEB_CHAT_BOOTSTRAP_URL);
    return fetch(url.replace(':8767/', ':' + port + '/'), {...options, headers: {...options.headers, Origin: origin}});
  },
  onState: state => {
    if (state.status === 'connected') controller.connect(state);
    else controller.disconnect();
  },
  onEvent: event => controller.apply(event, transport.snapshot()),
  onVisual: event => {
    assert.equal(event.turn_id, controller.snapshot().activeTurn.turnId);
    assert.equal(event.correlation_id, controller.snapshot().activeTurn.correlationId);
    assert.equal(event.session_id, transport.snapshot().sessionId);
    assert.equal(Object.hasOwn(event, 'revision'), false);
    visuals.push(event);
  },
});
async function until(predicate) {
  const deadline = Date.now() + 5000;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error('visual_loopback_timeout:' + transport.snapshot().status + ':' + visuals.length);
    await new Promise(resolve => setTimeout(resolve, 10));
  }
}
try {
  await transport.start();
  await until(() => transport.snapshot().confirmed);
  controller.beginTurn(transport.submit({text: 'private avatar fixture'}));
  await until(() => visuals.some(e => e.payload.mouth.open > .1));
  assert.ok(visuals.some(e => e.payload.expression?.action === 'happy'));
  const during = transport.snapshot().lastTurn;
  await until(() => visuals.filter(e => e.payload.mouth.open > .1).length >= 3);
  assert.equal(transport.snapshot().lastTurn.revision, during.revision);
  await until(() => controller.snapshot().voiceState === 'delivered');
  await until(() => visuals.some(e => e.payload.voice_state === 'delivered'));
  const final = visuals.at(-1).payload;
  assert.equal(final.mouth.open, 0);
  assert.equal(final.expression, null);
  assert.equal(controller.snapshot().activeTurn.text, 'Fixture visual reply');
  assert.equal(submits, 1);
  for (let i = 1; i < visuals.length; i++) assert.ok(visuals[i].visual_sequence > visuals[i - 1].visual_sequence);
  console.log('PRIVATE_AVATAR_NODE_PASS submits=1 mouth=true reaction=true terminal_silence=true chat_revision_independent=true');
} finally {
  transport.stop();
}
