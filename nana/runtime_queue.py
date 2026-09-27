"""Compatibility shim for phase modules extracted before runtime split.

Older phase files imported ``nana.runtime_queue`` as a module. The live runtime
now owns the singleton in ``nana.runtime.priority_queue`` and pending actions in
``nana.actions.pending``. Keep this shim narrow so those phase status functions
can read current state without reintroducing legacy main imports.
"""

from nana.actions.pending import pending_actions
from nana.runtime.priority_queue import Priority, RuntimePriorityQueue, RuntimeTask, runtime_queue


def snapshot():
    return runtime_queue.snapshot()


def mark_user_input():
    return runtime_queue.mark_user_input()


def set_user_active(active):
    return runtime_queue.set_user_active(active)


def enqueue(priority, name, source="unknown", payload=None):
    return runtime_queue.enqueue(priority, name, source=source, payload=payload)


def can_run(priority):
    return runtime_queue.can_run(priority)


def next_ready():
    return runtime_queue.next_ready()


def __getattr__(name):
    return getattr(runtime_queue, name)
