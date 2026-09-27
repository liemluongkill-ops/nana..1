from dataclasses import dataclass, field

from nana.actions.registry import ActionPermission, action_registry


@dataclass(frozen=True)
class BrokerDecision:
    action: str
    status: str
    permission: str
    reason: str
    requires_confirm: bool = False
    can_execute: bool = False
    details: dict = field(default_factory=dict)


class ActionBroker:
    def __init__(self, registry):
        self.registry = registry

    def evaluate(self, action_name, context=None):
        context = context or {}
        spec = self.registry.get(action_name)
        if not spec:
            return BrokerDecision(
                action=action_name,
                status="blocked",
                permission="unknown",
                reason="action_not_registered",
            )

        if spec.permission == ActionPermission.BLOCKED:
            return BrokerDecision(
                action=spec.name,
                status="blocked",
                permission=spec.permission.value,
                reason="permission_blocked",
            )

        if spec.permission == ActionPermission.SUGGEST_ONLY:
            return BrokerDecision(
                action=spec.name,
                status="suggest_only",
                permission=spec.permission.value,
                reason="suggestion_allowed_no_execution",
            )

        if spec.permission == ActionPermission.CONFIRM_FIRST:
            reason = self._confirm_block_reason(context)
            if reason:
                return BrokerDecision(
                    action=spec.name,
                    status="blocked",
                    permission=spec.permission.value,
                    reason=reason,
                    requires_confirm=True,
                    details=dict(context),
                )
            return BrokerDecision(
                action=spec.name,
                status="needs_confirm",
                permission=spec.permission.value,
                reason="confirmation_required",
                requires_confirm=True,
                details=dict(context),
            )

        if spec.permission == ActionPermission.READ_ONLY:
            return BrokerDecision(
                action=spec.name,
                status="allowed",
                permission=spec.permission.value,
                reason="read_only_allowed",
                can_execute=True,
                details=dict(context),
            )

        return BrokerDecision(
            action=spec.name,
            status="blocked",
            permission=spec.permission.value,
            reason="unknown_permission",
            details=dict(context),
        )

    @staticmethod
    def _confirm_block_reason(context):
        if not context.get("browser_available", False):
            return "browser_unavailable"
        if not context.get("browser_fresh", False):
            return "browser_snapshot_stale"
        if not context.get("active_app_is_edge", False):
            return "active_window_not_edge"
        if not context.get("active_window_valid", False):
            return "active_window_not_validated"
        return None


action_broker = ActionBroker(action_registry)
