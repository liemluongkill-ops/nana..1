"""nana.llm.gate — thin LLMGate facade for the new Nana runtime.

This module is the stable import path for new-architecture callers. The
actual HTTP client still lives in ``nana.brain.llmgate_client`` for now, so
legacy and new callers share one implementation instead of drifting apart.
"""
from __future__ import annotations


def call_llmgate(model_name, prompt, max_tokens=180, temperature=0.55):
    from nana.brain.llmgate_client import call_llmgate as _call_llmgate

    return _call_llmgate(
        model_name,
        prompt,
        max_tokens=max_tokens,
        temperature=temperature,
    )
