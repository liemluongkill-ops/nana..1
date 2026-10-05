// Run only from the Python fixture runner; no production endpoint is used.
import assert from 'node:assert/strict';
import {createCoreChatTransport, PRIVATE_WEB_CHAT_BOOTSTRAP_URL, PRIVATE_WEB_CHAT_WS_URL} from '../src/coreChatTransport.js';
import {createConversationController} from '../src/conversationController.js';
const port = Number(process.argv[2]);
assert.ok(Number.isInteger(port) && port > 0 && port !== 8767);
const origin = 'http://127.0.0.1:5174';
let currentSocket, submitted = 0, pongs = 0, connections = 0, runtimeSeen = false;
const controller = createConversationController();
class Socket extends WebSocket {
  constructor(url) {
    assert.equal(url, PRIVATE_WEB_CHAT_WS_URL);
    super(url.replace(':8767/', ':' + port + '/'), {headers: {Origin: origin}});
    currentSocket = this;
  }
  send(data) {
    const frame = JSON.parse(data);
    if (frame.type === 'chat.submit') submitted++;
    if (frame.type === 'session.pong') pongs++;
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
    if (state.status === 'connected') {
      if (controller.snapshot().connectionState !== 'connected') connections++;
      controller.connect(state);
    } else controller.disconnect();
  },
  onEvent: event => {
    if (event.type === 'runtime.state') runtimeSeen = true;
    return controller.apply(event, transport.snapshot());
  },
});
async function until(predicate, timeout = 5000) {
  const deadline = Date.now() + timeout;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error('loopback_condition_timeout:' + transport.snapshot().status);
    await new Promise(resolve => setTimeout(resolve, 10));
  }
}
try {
  await transport.start(); await until(() => transport.snapshot().confirmed && runtimeSeen);
  const ids = transport.submit({text: 'fixture private turn'}); controller.beginTurn(ids);
  await until(() => controller.snapshot().voiceState === 'delivered');
  assert.equal(controller.snapshot().turnState, 'complete');
  assert.equal(controller.snapshot().activeTurn.text, 'fixture final');
  currentSocket.close(4006);
  await until(() => transport.snapshot().status === 'disconnected');
  await transport.start();
  await until(() => connections >= 2 && !controller.snapshot().uncertain);
  assert.equal(controller.snapshot().voiceState, 'delivered');
  assert.equal(submitted, 1);
  const next = transport.submit({text: 'second fixture turn'}); controller.beginTurn(next);
  await until(() => controller.snapshot().voiceState === 'delivered');
  assert.equal(controller.snapshot().turnState, 'complete');
  await until(() => pongs > 0, 7000);
  assert.equal(submitted, 2);
  console.log('NODE_LOOPBACK_PASS requests=2 reconnect_no_replay=true heartbeat=true');
} finally {
  transport.stop();
}
