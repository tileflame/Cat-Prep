"""
background.py — keep slow work off the Tk event loop, safely.

Tk is single-threaded and its API is *not* thread-safe: calling ``widget.after``
(or anything else) from a worker thread can raise or corrupt the interpreter.
The only safe pattern is for the worker to leave its result somewhere and let
the main thread come and collect it.

So:

    worker thread   runs the function, pushes (callback, result, error) onto a queue
    main thread     a single repeating after() drains that queue and runs callbacks

No Tk object is ever touched off the main thread. The pump backs off to a slow
poll when there is nothing outstanding, so an idle app is not waking up 20 times
a second for no reason.
"""

from __future__ import annotations

import queue
import threading
import traceback

_JOBS: "queue.Queue" = queue.Queue()
_RESULTS: "queue.Queue" = queue.Queue()
_WORKER: threading.Thread | None = None
_LOCK = threading.Lock()
_SHUTDOWN = object()

DEBUG = False

# Poll fast while work is in flight, slowly when idle.
BUSY_INTERVAL_MS = 30
IDLE_INTERVAL_MS = 250

_pump_widget = None
_pump_job = None
_outstanding = 0
_outstanding_lock = threading.Lock()


def _run():
    while True:
        job = _JOBS.get()
        if job is _SHUTDOWN:
            _JOBS.task_done()
            return
        func, args, kwargs, on_done = job
        try:
            result, error = func(*args, **kwargs), None
        except Exception as exc:
            result, error = None, exc
            if DEBUG:
                traceback.print_exc()
        if on_done is not None:
            _RESULTS.put((on_done, result, error))
        _JOBS.task_done()


def _ensure_worker():
    global _WORKER
    with _LOCK:
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER = threading.Thread(target=_run, name="catsat-worker", daemon=True)
            _WORKER.start()


def submit(func, *args, on_done=None, widget=None, **kwargs) -> None:
    """
    Run ``func`` on the worker thread.

    ``on_done(result, error)`` runs later on the Tk thread, via the pump.
    ``widget`` is accepted for call-site readability but is not used to schedule
    anything — that is exactly the unsafe thing this module exists to avoid.
    """
    global _outstanding
    _ensure_worker()
    with _outstanding_lock:
        _outstanding += 1
    _JOBS.put((func, args, kwargs, on_done))


def drain(limit: int = 12) -> int:
    """
    Run up to ``limit`` finished callbacks. Main thread only.

    Bounded so a burst of completions can never block a frame; whatever is left
    is picked up on the next tick.
    """
    global _outstanding
    ran = 0
    while ran < limit:
        try:
            on_done, result, error = _RESULTS.get_nowait()
        except queue.Empty:
            break
        with _outstanding_lock:
            _outstanding = max(0, _outstanding - 1)
        try:
            on_done(result, error)
        except Exception:
            if DEBUG:
                traceback.print_exc()
        finally:
            _RESULTS.task_done()
        ran += 1
    return ran


def install_pump(widget) -> None:
    """Start the main-thread pump. Called once, by the app, at startup."""
    global _pump_widget
    _pump_widget = widget
    _schedule_pump(IDLE_INTERVAL_MS)


def _schedule_pump(interval_ms: int) -> None:
    global _pump_job
    widget = _pump_widget
    if widget is None:
        return
    try:
        if not widget.winfo_exists():
            return
        _pump_job = widget.after(interval_ms, _pump)
    except Exception:
        pass


def _pump() -> None:
    drain()
    with _outstanding_lock:
        busy = _outstanding > 0
    _schedule_pump(BUSY_INTERVAL_MS if busy else IDLE_INTERVAL_MS)


def pending() -> int:
    with _outstanding_lock:
        return _outstanding


def wait_idle(timeout: float = 2.0) -> None:
    """Block until queued work finishes. For tests and shutdown, not the UI."""
    try:
        _JOBS.join()
    except Exception:
        pass


def shutdown(timeout: float = 0.5) -> None:
    """Stop the worker and the pump. Called when the app closes."""
    global _WORKER, _pump_widget, _pump_job
    widget, job = _pump_widget, _pump_job
    _pump_widget = _pump_job = None
    if widget is not None and job is not None:
        try:
            widget.after_cancel(job)
        except Exception:
            pass
    with _LOCK:
        worker = _WORKER
        _WORKER = None
    if worker is not None and worker.is_alive():
        _JOBS.put(_SHUTDOWN)
        worker.join(timeout=timeout)
