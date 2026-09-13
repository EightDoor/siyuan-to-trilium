"""统一进度输出。"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ProgressState:
    stage: str = "init"
    current: int = 0
    total: int = 0
    detail: str = ""
    started_at: float = field(default_factory=time.time)


class ProgressReporter:
    """阶段化进度输出器。"""

    def __init__(self, enabled: bool = True, stream=None):
        self._enabled = enabled
        self._stream = stream or sys.stderr
        self._state = ProgressState()
        self._lock = threading.Lock()
        self._last_line_len = 0

    @property
    def state(self) -> ProgressState:
        return self._state

    def update(self, stage: str, current: int, total: int, detail: str = "") -> None:
        with self._lock:
            self._state.stage = stage
            self._state.current = current
            self._state.total = total
            self._state.detail = detail
        self._render()

    def stage(self, name: str, detail: str = "") -> None:
        self.update(name, 0, 0, detail)

    def finish(self, message: str = "完成") -> None:
        with self._lock:
            self._state.detail = message
        self._render(final=True)

    def _render(self, final: bool = False) -> None:
        if not self._enabled:
            return
        elapsed = time.time() - self._state.started_at
        if self._state.total > 0:
            pct = 100.0 * self._state.current / max(self._state.total, 1)
            line = (
                f"[{elapsed:6.1f}s] {self._state.stage:<24} "
                f"{self._state.current:>6}/{self._state.total:<6} ({pct:5.1f}%) {self._state.detail}"
            )
        else:
            line = f"[{elapsed:6.1f}s] {self._state.stage:<24}                    {self._state.detail}"
        with self._lock:
            pad = max(self._last_line_len - len(line), 0)
            self._stream.write("\r" + line + " " * pad)
            self._stream.flush()
            self._last_line_len = len(line)
            if final:
                self._stream.write("\n")
                self._stream.flush()
                self._last_line_len = 0