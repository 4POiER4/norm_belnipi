"""Фоновая проверка живёт независимо от HTTP/SSE-подключения браузера."""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Callable, Iterable

log = logging.getLogger(__name__)


class BackgroundStream:
    def __init__(self, conversation_id: str):
        self.id = str(uuid.uuid4())
        self.conversation_id = conversation_id
        self.started_at = time.time()
        self.finished_at: float | None = None
        self._condition = threading.Condition()
        self._events: list[str] = []
        self._status = {"status": "Проверка запущена на сервере…"}
        self._status_version = 0
        self._done = False
        self._error = ""

    def publish(self, frame: str) -> None:
        # Статусы заменяются, а не накапливаются во время отсутствия клиента.
        if not frame.startswith("data: ") or frame.strip() == "data: [DONE]":
            return
        payload = json.loads(frame[6:].strip())
        with self._condition:
            if payload.get("status"):
                self._status = payload
                self._status_version += 1
            else:
                self._events.append(frame)
            if payload.get("error"):
                self._error = str(payload["error"])
            self._condition.notify_all()

    def start(self, producer: Callable[[], Iterable[str]]) -> None:
        def run():
            try:
                for frame in producer():
                    self.publish(frame)
            except Exception:
                log.exception("background check failed job=%s conversation=%s", self.id, self.conversation_id)
                self.publish('data: ' + json.dumps({"error": "Не удалось завершить или сохранить проверку"},
                                                  ensure_ascii=False) + '\n\n')
            finally:
                with self._condition:
                    self._done = True
                    self.finished_at = time.time()
                    self._condition.notify_all()
                log.info("background check finished job=%s conversation=%s state=%s elapsed=%.1fs",
                         self.id, self.conversation_id, self.snapshot()["state"],
                         self.finished_at - self.started_at)
        threading.Thread(target=run, name=f"normative-{self.id[:8]}", daemon=True).start()

    def snapshot(self) -> dict:
        with self._condition:
            return {"id": self.id, "state": "failed" if self._done and self._error
                    else "completed" if self._done else "running",
                    "status": self._status.get("status", ""), "error": self._error,
                    "started_at": self.started_at, "finished_at": self.finished_at}

    def stream(self):
        index, version = 0, -1
        yield 'data: ' + json.dumps({"job_id": self.id}) + '\n\n'
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._done or index < len(self._events)
                                         or version != self._status_version, timeout=12)
                frames = self._events[index:]
                index = len(self._events)
                status = dict(self._status)
                version = self._status_version
                done = self._done
            if not done:
                yield 'data: ' + json.dumps(status, ensure_ascii=False) + '\n\n'
            yield from frames
            if done:
                # producer закончился: в том числе сохранение результата и уборка файла.
                yield 'data: [DONE]\n\n'
                return


class JobRegistry:
    def __init__(self, retention_sec: float = 3600):
        self._lock = threading.Lock()
        self._jobs: dict[str, BackgroundStream] = {}
        self.retention_sec = retention_sec

    def create(self, conversation_id: str) -> BackgroundStream:
        with self._lock:
            self._jobs = {key: job for key, job in self._jobs.items()
                          if job.finished_at is None or time.time() - job.finished_at < self.retention_sec}
            old = self._jobs.get(conversation_id)
            if old and old.snapshot()["state"] == "running":
                raise ValueError("В этом чате уже выполняется проверка")
            job = BackgroundStream(conversation_id)
            self._jobs[conversation_id] = job
            return job

    def get(self, conversation_id: str) -> BackgroundStream | None:
        with self._lock:
            return self._jobs.get(conversation_id)

    def discard(self, job: BackgroundStream) -> None:
        with self._lock:
            if self._jobs.get(job.conversation_id) is job:
                del self._jobs[job.conversation_id]
