"""Scoped signal handling for CLI-owned native child processes."""
from contextlib import contextmanager
import signal
import threading


@contextmanager
def termination_as_interrupt():
    """Let a CLI SIGTERM take the same cleanup path as Ctrl-C, then restore it."""
    enabled = threading.current_thread() is threading.main_thread()
    previous = signal.getsignal(signal.SIGTERM) if enabled else None
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        if not stopping:
            stopping = True
            raise KeyboardInterrupt()

    if enabled:
        signal.signal(signal.SIGTERM, stop)
    try:
        yield
    finally:
        if enabled:
            signal.signal(signal.SIGTERM, previous)
