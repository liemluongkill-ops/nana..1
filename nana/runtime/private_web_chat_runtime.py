"""Compose private web chat around existing Core owners; import has no I/O."""
import asyncio
from dataclasses import dataclass
from uuid import uuid4

@dataclass
class _PrivateWebChatRuntime:
    """One Core-owned private bridge composed around existing runtime owners."""

    voice: object
    coordinator: object
    server: object
    receipt_ledger: object
    visual_signals: object
    visual_task: object = None
    started: bool = False
    stopped: bool = False

    async def start(self) -> bool:
        if self.stopped:
            return False
        result = await self.server.start()
        self.started = bool(result)
        if self.started and self.visual_task is None:
            self.visual_task = asyncio.create_task(self._pump_visuals(), name='nana-private-visuals')
        return self.started

    async def _pump_visuals(self):
        from .private_avatar_signals import POLL_SECONDS
        while True:
            for context, sequence, payload in self.visual_signals.collect():
                self.coordinator.publish_avatar_signal(context, sequence, payload)
            await asyncio.sleep(POLL_SECONDS)

    async def stop(self, drain_seconds: float = 15.0) -> None:
        if self.stopped:
            return
        deadline = asyncio.get_running_loop().time() + max(0., float(drain_seconds))
        self.server.begin_shutdown()
        self.coordinator.begin_shutdown()
        await self.coordinator.drain(float(drain_seconds))
        while self.receipt_ledger.active():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                self.receipt_ledger.expire_active('shutdown_timeout')
                break
            await asyncio.sleep(min(.02, remaining))
        await asyncio.sleep(0)
        if self.visual_task is not None:
            self.visual_task.cancel()
            await asyncio.gather(self.visual_task, return_exceptions=True)
        self.visual_signals.close()
        visual_setter = getattr(self.voice, 'set_private_avatar_signals', None)
        if callable(visual_setter):
            visual_setter(None)
        await self.server.stop(max(0., deadline - asyncio.get_running_loop().time()))
        self.coordinator.clear()
        self.receipt_ledger.clear()
        setter = getattr(self.voice, 'set_private_voice_receipt_ledger', None)
        if callable(setter):
            setter(None)
        self.started = False
        self.stopped = True


def _build_private_web_chat_runtime(
    *,
    enabled: bool,
    voice,
    turn_lock,
    dispatch_turn_unlocked,
    launcher,
    ownership_verifier,
    server_factory=None,
    server_epoch: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8767,
    lock_wait_seconds: float = 5.0,
    turn_timeout_seconds: float = 120.0,
    shutdown_drain_seconds: float = 15.0,
    loop=None,
):
    """Compose the web adapter without constructing another Core owner."""

    if not enabled:
        return None
    if host != "127.0.0.1" or type(port) is not int or port not in {0, 8767}:
        raise ValueError("private web chat must use 127.0.0.1:8767")
    from nana.runtime.private_turn_coordinator import PrivateTurnCoordinator
    from nana.runtime.private_voice_receipts import PrivateVoiceReceiptLedger
    from nana.runtime.private_voice_receipts import PrivateVoiceContext
    from nana.runtime.private_avatar_signals import PrivateAvatarSignals
    from nana.runtime.avatar_reply_turn import private_avatar_scope
    from nana.runtime.private_web_chat_server import PrivateWebChatServer

    server_ref: dict[str, object] = {}

    def event_sink(session, event):
        server = server_ref.get("server")
        enqueue = getattr(server, "enqueue_event", None)
        if callable(enqueue):
            return enqueue(session, event)
        return None

    coordinator_ref: dict[str, PrivateTurnCoordinator] = {}
    owner_loop = loop or asyncio.get_event_loop()

    def receipt_sink(receipt):
        visual_signals.on_receipt(receipt)
        coordinator = coordinator_ref.get("coordinator")
        callback = getattr(coordinator, "on_private_voice_receipt", None)
        if callable(callback):
            owner_loop.call_soon_threadsafe(callback, receipt)

    receipt_ledger = PrivateVoiceReceiptLedger(event_sink=receipt_sink)
    visual_signals = PrivateAvatarSignals(receipt_ledger)
    visual_setter = getattr(voice, 'set_private_avatar_signals', None)
    if callable(visual_setter):
        visual_setter(visual_signals)
    setter = getattr(voice, "set_private_voice_receipt_ledger", None)
    if callable(setter):
        setter(receipt_ledger)

    def dispatch_scope(identity):
        context = PrivateVoiceContext(identity.server_epoch, identity.session_id,
                                      identity.turn_id, identity.correlation_id)
        visual_signals.begin_turn(context)
        return private_avatar_scope(visual_signals, context)

    def visual_event_sink(session, event):
        server = server_ref.get('server')
        enqueue = getattr(server, 'enqueue_visual_event', None)
        return enqueue(session, event) if callable(enqueue) else False

    coordinator = PrivateTurnCoordinator(
        turn_lock=turn_lock,
        dispatch_turn_unlocked=dispatch_turn_unlocked,
        server_epoch=server_epoch or str(uuid4()),
        event_sink=event_sink,
        dispatch_scope=dispatch_scope,
        visual_event_sink=visual_event_sink,
        lock_wait_seconds=float(lock_wait_seconds),
        wall_deadline_seconds=float(turn_timeout_seconds),
        close_session=lambda session, code: server_ref['server'].close_session(session, code),
    )
    coordinator_ref["coordinator"] = coordinator

    async def application_handler(session, frame):
        if frame.message_type == "chat.submit":
            before = coordinator.lookup((coordinator.server_epoch, session.client_instance_id, frame.turn_id))
            snapshot = await coordinator.submit(session, frame)
            if before is not None or snapshot.turn_state == 'failed':
                coordinator.publish_snapshot(session, snapshot)
        elif frame.message_type == "chat.reconcile":
            snapshot = await coordinator.reconcile(session, frame)
            coordinator.publish_snapshot(session, snapshot)
        return None

    factory = server_factory or PrivateWebChatServer
    server = factory(
        server_epoch=coordinator.server_epoch,
        launcher=launcher,
        ownership_verifier=ownership_verifier,
        runtime_state_provider=coordinator.runtime_state,
        application_handler=application_handler,
        host=host,
        port=port,
        bridge_enabled=True,
    )
    server_ref["server"] = server
    return _PrivateWebChatRuntime(
        voice=voice,
        coordinator=coordinator,
        server=server,
        receipt_ledger=receipt_ledger,
        visual_signals=visual_signals,
    )
