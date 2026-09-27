from dataclasses import dataclass
from enum import Enum


class ActionPermission(str, Enum):
    READ_ONLY = "read-only"
    SUGGEST_ONLY = "suggest-only"
    CONFIRM_FIRST = "confirm-first"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ActionSpec:
    name: str
    permission: ActionPermission
    cost_estimate: str = "low"
    rollback: str = "none"
    description: str = ""


class ActionRegistry:
    def __init__(self):
        self._actions = {}

    def register(self, spec):
        self._actions[spec.name] = spec

    def get(self, name):
        return self._actions.get(name)

    def list_actions(self):
        return [self._actions[name] for name in sorted(self._actions)]

    def can_execute_without_confirm(self, name):
        spec = self.get(name)
        return bool(spec and spec.permission == ActionPermission.READ_ONLY)


action_registry = ActionRegistry()

action_registry.register(ActionSpec(
    name="browser.read_context",
    permission=ActionPermission.READ_ONLY,
    cost_estimate="low",
    rollback="none",
    description="Read current browser title, URL, kind, heading, meta, selected text.",
))
action_registry.register(ActionSpec(
    name="browser.suggest_next_step",
    permission=ActionPermission.SUGGEST_ONLY,
    cost_estimate="low",
    rollback="none",
    description="Suggest a next step without touching the browser.",
))
action_registry.register(ActionSpec(
    name="browser.scroll",
    permission=ActionPermission.CONFIRM_FIRST,
    cost_estimate="low",
    rollback="limited",
    description="Scroll the active browser tab after confirmation.",
))
action_registry.register(ActionSpec(
    name="browser.click",
    permission=ActionPermission.CONFIRM_FIRST,
    cost_estimate="medium",
    rollback="limited",
    description="Click an element only after confirmation and active-tab validation.",
))
action_registry.register(ActionSpec(
    name="browser.type",
    permission=ActionPermission.CONFIRM_FIRST,
    cost_estimate="medium",
    rollback="limited",
    description="Type text only after confirmation.",
))
action_registry.register(ActionSpec(
    name="social.type_draft",
    permission=ActionPermission.CONFIRM_FIRST,
    cost_estimate="medium",
    rollback="limited",
    description="Type an approved social draft into the focused social composer after confirmation; posting remains blocked.",
))
action_registry.register(ActionSpec(
    name="purchase.checkout",
    permission=ActionPermission.BLOCKED,
    cost_estimate="high",
    rollback="none",
    description="Checkout or purchase flow is blocked.",
))
action_registry.register(ActionSpec(
    name="message.send",
    permission=ActionPermission.BLOCKED,
    cost_estimate="high",
    rollback="none",
    description="Sending messages is blocked until an explicit future policy exists.",
))
