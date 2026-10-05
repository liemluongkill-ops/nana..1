import {PROTOCOL_NAME, LIMITS, exact, uuid, capabilities, buildFrame,
  buildChatSubmit, buildReconcile, decodeCoreEvent, decodeAvatarSignal} from "./coreChatProtocol.js";

export const PRIVATE_WEB_CHAT_BOOTSTRAP_URL = "http://127.0.0.1:8767/v1/web-chat/bootstrap";
export const PRIVATE_WEB_CHAT_WS_URL = "ws://127.0.0.1:8767/v1/web-chat";
const RETRY_DELAYS = [250, 500, 1000, 2000, 5000];
const TERMINAL_CLOSE = new Set([1000, 4001, 4002, 4003, 4004, 4008, 4009, 4010]);

export function createCoreChatTransport({
  fetchImpl = globalThis.fetch, WebSocketImpl = globalThis.WebSocket,
  randomUuid = () => globalThis.crypto.randomUUID(), clientInstanceId = randomUuid(),
  onEvent = () => {}, onState = () => {}, onVisual = () => {}, scheduler = globalThis.setTimeout,
  cancelScheduler = globalThis.clearTimeout, now = () => performance.now(),
} = {}) {
  uuid(clientInstanceId);
  let status = "disconnected", errorCode = null, running = false, context = null;
  let socket = null, grant = null, phase = "idle", nonce = null;
  let generation = 0, connecting = null, retryIndex = 0, retryAt = null, retries = 0;
  let retryTimer = null, idleTimer = null, handshakeTimer = null, abort = null;
  let turn = null, eventSequence = 0, needsReconcile = false, reconciling = false;
  const notify = (fn, value) => { try { return fn(value); } catch { return undefined; } };
  const activeTurn = () => turn && (needsReconcile || !["complete", "failed", "unknown"].includes(turn.state)
    || ["queued", "speaking"].includes(turn.voiceState));
  function snapshot() {
    return {status, errorCode, serverEpoch: context?.serverEpoch || null,
      sessionId: context?.sessionId || null, clientInstanceId, confirmed: phase === "confirmed",
      capabilityPresent: grant !== null, pendingTurn: activeTurn() ? {...turn} : null,
      lastTurn: turn ? {...turn} : null};
  }
  function publish(next, reason = null) { status = next; errorCode = reason; notify(onState, snapshot()); }
  function clearTimer(name) {
    const id = name === "retry" ? retryTimer : name === "idle" ? idleTimer : handshakeTimer;
    if (id !== null) cancelScheduler(id);
    if (name === "retry") retryTimer = null;
    else if (name === "idle") idleTimer = null;
    else handshakeTimer = null;
  }
  function send(frame) {
    if (!socket || socket.readyState !== 1) throw new Error("core_chat_disconnected");
    socket.send(JSON.stringify(frame));
  }
  function forgetGrant() { grant = null; nonce = null; retryAt = null; retries = 0; }
  function scheduleReconnect() {
    if (!running || retryIndex >= RETRY_DELAYS.length || retryTimer !== null) return;
    retryTimer = scheduler(() => { retryTimer = null; void connect(); }, RETRY_DELAYS[retryIndex++]);
  }
  function reject(code = "protocol_error", close = 4004) {
    forgetGrant(); publish("degraded", code);
    const ws = socket;
    if (ws) ws.close(close);
  }
  function refreshIdle() {
    clearTimer("idle");
    idleTimer = scheduler(() => {
      idleTimer = null;
      if (socket && phase === "confirmed") socket.close(4006);
    }, 20000);
  }
  function receive(value) {
    if (!value || value.protocol !== PROTOCOL_NAME) throw new Error("protocol_error");
    if (phase === "hello") {
      exact(value, ["protocol", "type", "server_epoch", "provisional_session_id", "handshake_nonce", "limits", "capabilities"]);
      if (value.type !== "session.welcome" || value.server_epoch !== grant.server_epoch || value.handshake_nonce !== nonce) throw new Error("protocol_error");
      uuid(value.provisional_session_id); capabilities(value.capabilities);
      exact(value.limits, Object.keys(LIMITS));
      for (const key of Object.keys(LIMITS)) if (value.limits[key] !== LIMITS[key]) throw new Error("protocol_error");
      context = {serverEpoch: value.server_epoch, sessionId: value.provisional_session_id, clientInstanceId};
      phase = "ready";
      send({type: "session.ready", protocol: PROTOCOL_NAME, server_epoch: context.serverEpoch,
        provisional_session_id: context.sessionId, handshake_nonce: nonce});
      return;
    }
    if (phase === "ready") {
      exact(value, ["protocol", "type", "server_epoch", "session_id", "event_sequence"]);
      if (value.type !== "session.active" || value.server_epoch !== context.serverEpoch
          || value.session_id !== context.sessionId || value.event_sequence !== 1) throw new Error("protocol_error");
      eventSequence = value.event_sequence;
      phase = "ack";
      send({type: "session.active_ack", protocol: PROTOCOL_NAME,
        server_epoch: context.serverEpoch, session_id: context.sessionId, handshake_nonce: nonce});
      return;
    }
    if (phase === "ack") {
      exact(value, ["protocol", "type", "server_epoch", "session_id"]);
      if (value.type !== "session.confirmed" || value.server_epoch !== context.serverEpoch || value.session_id !== context.sessionId) throw new Error("protocol_error");
      phase = "confirmed"; forgetGrant(); clearTimer("handshake"); retryIndex = 0;
      refreshIdle(); publish("connected");
      requestRuntimeState();
      if (turn) reconcile();
      return;
    }
    if (phase !== "confirmed") throw new Error("handshake_required");
    if (value.type === 'avatar.signal') {
      // Optional visuals cannot change chat revisions, delivery truth, or
      // admission. Malformed/stale visuals are dropped; the mouth expires.
      let visual;
      try { visual = decodeAvatarSignal(value, context); } catch { return; }
      if (!turn || visual.turn_id !== turn.turnId || visual.correlation_id !== turn.correlationId) return;
      refreshIdle();
      notify(onVisual, visual);
      return;
    }
    const event = decodeCoreEvent(value, context);
    refreshIdle();
    if (event.type === "session.ping") {
      send({type: "session.pong", protocol: PROTOCOL_NAME}); return;
    }
    if (event.type === "runtime.state") { notify(onEvent, event); return; }
    if (event.event_sequence <= eventSequence) return;
    eventSequence = event.event_sequence;
    if (!turn || event.turn_id !== turn.turnId || event.correlation_id !== turn.correlationId) return;
    if (event.type !== "turn.snapshot" && event.revision <= turn.revision) return;
    const decision = notify(onEvent, event);
    if (decision?.action === "reconcile") { reconcile(); return; }
    if (decision?.action === "protocol_error" || decision?.action === "ignored") return;
    turn.revision = event.revision;
    if (event.type === "turn.state") turn.state = event.payload.state;
    if (event.type === "voice.state") turn.voiceState = event.payload.state;
    if (event.type === "turn.snapshot") {
      reconciling = false; needsReconcile = false;
      turn.state = event.payload.turn_state; turn.voiceState = event.payload.voice_state;
      if (event.payload.reason_code === "epoch_changed") {
        turn.state = "unknown"; turn.voiceState = "unknown";
        publish("connected", "epoch_changed");
      }
    }
    notify(onState, snapshot());
  }
  async function connect() {
    if (!running || socket || connecting) return connecting;
    const current = generation;
    connecting = (async () => {
      try {
        if (grant && (retries >= 1 || retryAt === null || now() > retryAt)) forgetGrant();
        if (!grant) {
          publish("connecting");
          abort = new AbortController();
          handshakeTimer = scheduler(() => abort?.abort(), 10000);
          // No Content-Type/body: a simple CORS POST needs no preflight route.
          const response = await fetchImpl(PRIVATE_WEB_CHAT_BOOTSTRAP_URL,
            {method: "POST", headers: {Accept: "application/json"}, cache: "no-store", signal: abort.signal});
          clearTimer("handshake");
          if (!running || current !== generation) return;
          if (!response.ok) throw new Error("bootstrap_failed");
          const value = await response.json();
          if (!running || current !== generation) return;
          exact(value, ["protocol", "server_epoch", "handshake_id", "capability", "expires_in_ms"]);
          if (value.protocol !== PROTOCOL_NAME || value.expires_in_ms !== 10000
              || typeof value.capability !== "string" || !value.capability || value.capability.length > 256) throw new Error("invalid_bootstrap");
          uuid(value.server_epoch);
          if (typeof value.handshake_id !== 'string' || !/^[A-Za-z0-9_-]{16,256}$/.test(value.handshake_id)) throw new Error('invalid_bootstrap');
          grant = value; nonce = randomUuid(); retries = 0;
        } else retries += 1;
        const ws = new WebSocketImpl(PRIVATE_WEB_CHAT_WS_URL);
        socket = ws; phase = "opening";
        handshakeTimer = scheduler(() => { if (socket === ws) reject("handshake_timeout", 4002); }, 10000);
        ws.onopen = () => {
          if (socket !== ws || !running) return;
          phase = "hello";
          send({type: "session.hello", protocol: PROTOCOL_NAME, handshake_id: grant.handshake_id,
            capability: grant.capability, handshake_nonce: nonce, client_instance_id: clientInstanceId});
          publish("handshaking");
        };
        ws.onmessage = ({data}) => {
          if (socket !== ws || !running) return;
          try {
            if (typeof data !== "string" || new TextEncoder().encode(data).byteLength > 65536) throw new Error("invalid_frame");
            receive(JSON.parse(data));
          } catch { reject(); }
        };
        ws.onclose = ({code = 1006}) => {
          if (socket !== ws) return;
          socket = null; clearTimer("idle"); clearTimer("handshake");
          if (phase !== "confirmed" && grant && retries === 0 && !TERMINAL_CLOSE.has(code)) retryAt = now() + 5000;
          else forgetGrant();
          phase = "idle"; reconciling = false; needsReconcile = !!turn;
          publish(running ? "disconnected" : "stopped", code === 4009 ? "epoch_changed" : "connection_closed");
          if (!TERMINAL_CLOSE.has(code)) scheduleReconnect();
        };
        ws.onerror = () => { if (socket === ws) publish("degraded", "connection_failed"); };
      } catch {
        clearTimer('handshake');
        if (running && current === generation) {
          forgetGrant(); publish("disconnected", "bootstrap_failed"); scheduleReconnect();
        }
      } finally { if (current === generation) connecting = null; }
    })();
    return connecting;
  }
  async function start() {
    if (!running || retryIndex >= RETRY_DELAYS.length) retryIndex = 0;
    running = true; clearTimer("retry");
    await connect(); return snapshot();
  }
  function submit({turnId = randomUuid(), correlationId = randomUuid(), text}) {
    if (phase !== "confirmed") throw new Error("core_chat_not_confirmed");
    if (activeTurn()) throw new Error("turn_busy");
    const frame = buildChatSubmit({context, turnId, correlationId, text});
    turn = {turnId, correlationId, serverEpoch: context.serverEpoch, revision: 0, state: "accepted", voiceState: "idle"};
    needsReconcile = false; reconciling = false;
    try { send(frame); } catch (error) { needsReconcile = true; throw error; }
    notify(onState, snapshot());
    return {turnId, correlationId};
  }
  function reconcile() {
    if (!turn || phase !== "confirmed" || reconciling) return false;
    send(buildReconcile({context, turnServerEpoch: turn.serverEpoch, turnId: turn.turnId,
      correlationId: turn.correlationId, knownRevision: turn.revision}));
    reconciling = true; return true;
  }
  function requestRuntimeState() {
    if (phase !== "confirmed") return false;
    send(buildFrame({context, turnId: randomUuid(), correlationId: randomUuid(), type: "runtime.state.get", payload: {}}));
    return true;
  }
  function stop() {
    running = false; generation += 1;
    connecting = null;
    for (const name of ["retry", "idle", "handshake"]) clearTimer(name);
    abort?.abort(); abort = null;
    const ws = socket; socket = null; phase = "idle";
    ws?.close(1000);
    forgetGrant(); turn = null; context = null; eventSequence = 0; needsReconcile = false; reconciling = false;
    publish("stopped");
  }
  return Object.freeze({start, submit, reconcile, requestRuntimeState, stop, snapshot});
}
