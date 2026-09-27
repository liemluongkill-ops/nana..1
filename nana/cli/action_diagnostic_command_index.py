"""Lightweight command index for action/admin/diagnostic router."""

from __future__ import annotations

_ACTION_DIAGNOSTIC_EXACT_COMMANDS = frozenset({
    '/',
    '/action-cancel',
    '/action-confirm',
    '/action-plan',
    '/action-propose',
    '/action-propose-edge',
    '/actions',
    '/br',
    '/br-deep',
    '/broker-test',
    '/broker-test-edge',
    '/browser',
    '/browser-refresh',
    '/browser-refresh-deep',
    '/cancel',
    '/classify-expect',
    '/classify-text',
    '/confirm',
    '/context-budget',
    '/context-confidence',
    '/context-evidence',
    '/context-preview',
    '/context-priority',
    '/context-recovery',
    '/dom',
    '/draft-cancel',
    '/draft-confirm',
    '/draft-quality',
    '/draft-quality-gate',
    '/draft-quality-test',
    '/draft-show',
    '/drafts',
    '/evidence-trace',
    '/expression-policy',
    '/focus',
    '/fx',
    '/intent-test',
    '/llmgate-test',
    '/next-step',
    '/pa',
    '/pending',
    '/pending-action',
    '/phase3',
    '/phase4',
    '/presence',
    '/presence-off',
    '/presence-on',
    '/presence-reset',
    '/privacy-test',
    '/queue',
    '/reaction-test',
    '/reconcile-check',
    '/reconcile-expect',
    '/reconcile-guard-status',
    '/reconcile-guards',
    '/refine-auto-test',
    '/refine-guard-test',
    '/refine-test',
    '/route-test',
    '/social-classify',
    '/social-draft-test',
    '/social-draft-vision',
    '/social-guard-fails',
    '/social-guard-failures',
    '/social-guard-status',
    '/social-policy',
    '/social-target',
    '/speech-shape-test',
    '/suggest',
    '/target-status',
    '/time',
    '/vc',
    '/video-context',
    '/vision-cache',
    '/vision-cache-clear',
    '/vision-describe',
    '/vision-preview',
    '/vts-expression-policy',
    'social-draft-test',
    'social-draft-vision',
})

_ACTION_DIAGNOSTIC_PREFIX_COMMANDS = frozenset({
    '/action-cancel',
    '/action-confirm',
    '/action-propose',
    '/action-propose-edge',
    '/broker-test',
    '/broker-test-edge',
    '/cancel',
    '/classify-expect',
    '/classify-text',
    '/confirm',
    '/context-budget',
    '/context-preview',
    '/draft-cancel',
    '/draft-confirm',
    '/draft-quality',
    '/draft-quality-gate',
    '/draft-quality-test',
    '/draft-show',
    '/intent-test',
    '/llmgate-test',
    '/privacy-test',
    '/reaction-test',
    '/reconcile-expect',
    '/refine-auto-test',
    '/refine-guard-test',
    '/refine-test',
    '/route-test',
    '/social-classify',
    '/social-draft-test',
    '/social-draft-vision',
    '/speech-shape-test',
})


def _normalize_action_diagnostic_command(text: str) -> str:
    text = str(text or '').strip().lower()
    if text.startswith('//'):
        text = '/' + text.lstrip('/')
    return text


def is_action_diagnostic_command(text_lower: str) -> bool:
    text = _normalize_action_diagnostic_command(text_lower)
    if not text:
        return False
    return text in _ACTION_DIAGNOSTIC_EXACT_COMMANDS or any(
        text.startswith(prefix) for prefix in _ACTION_DIAGNOSTIC_PREFIX_COMMANDS
    )


__all__ = ['is_action_diagnostic_command']
