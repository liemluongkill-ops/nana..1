import time
from dataclasses import dataclass, field
from enum import IntEnum
import itertools
import threading


class Priority(IntEnum):
    P0_USER = 0
    P1_PROACTIVE = 1
    P2_BACKGROUND = 2


@dataclass(order=True)
class RuntimeTask:
    priority: int
    created_at: float
    name: str = field(compare=False)
    id: int = field(compare=False, default=0)
    source: str = field(compare=False, default="unknown")
    payload: dict = field(compare=False, default_factory=dict)
    status: str = field(compare=False, default="queued")
    defer_reason: str = field(compare=False, default="")


class RuntimePriorityQueue:
    def __init__(self, max_tasks=50, recent_user_window=1.5):
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self.max_tasks = max_tasks
        self.recent_user_window = recent_user_window
        self.tasks = []
        self.active_p0 = 0
        self.last_user_input_at = 0.0

    def mark_user_input(self):
        with self._lock:
            self.last_user_input_at = time.time()

    def set_user_active(self, active):
        with self._lock:
            self.active_p0 = 1 if active else 0

    def enqueue(self, priority, name, source="unknown", payload=None):
        task = RuntimeTask(
            priority=int(priority),
            created_at=time.time(),
            id=next(self._ids),
            name=name,
            source=source,
            payload=payload or {},
        )
        with self._lock:
            self.tasks.append(task)
            self.tasks.sort()
            del self.tasks[self.max_tasks:]
        return task

    def can_run(self, priority):
        with self._lock:
            return self._can_run_locked(priority)

    def _can_run_locked(self, priority, now=None):
        now = now or time.time()
        if int(priority) == int(Priority.P0_USER):
            return True, "ready"
        if self.active_p0:
            return False, "p0_active"
        if now - self.last_user_input_at < self.recent_user_window:
            return False, "recent_user_input"
        return True, "ready"

    def next_ready(self):
        with self._lock:
            for task in list(self.tasks):
                allowed, reason = self._can_run_locked(task.priority)
                if allowed:
                    self.tasks.remove(task)
                    task.status = "running"
                    return task
                task.status = "deferred"
                task.defer_reason = reason
        return None

    def snapshot(self):
        with self._lock:
            now = time.time()
            return {
                "active_p0": self.active_p0,
                "last_user_input_age": None if not self.last_user_input_at else max(0.0, now - self.last_user_input_at),
                "max_tasks": self.max_tasks,
                "recent_user_window": self.recent_user_window,
                "queued": [
                    {
                        "id": task.id,
                        "priority": Priority(task.priority).name,
                        "name": task.name,
                        "source": task.source,
                        "status": task.status,
                        "defer_reason": task.defer_reason,
                        "age": max(0.0, now - task.created_at),
                    }
                    for task in self.tasks
                ],
            }


runtime_queue = RuntimePriorityQueue()
