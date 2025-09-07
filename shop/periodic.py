import os
import threading
from django.core.management import call_command
from django.core.cache import cache

_INTERVAL_SECONDS = 30 * 60  # 30 minutes
_THREAD_NAME = "zp-unverified-scheduler"
_started = False
_stop = threading.Event()

def _run_once():
    lock_key = "zp_unverified_lock"
    if not cache.add(lock_key, "1", timeout=25 * 60):  # 25min TTL
        return
    try:
        call_command("zp_verify_unverified")
    finally:
        cache.delete(lock_key)

def _loop():
    _run_once()
    while not _stop.wait(_INTERVAL_SECONDS):
        _run_once()

def start():
    global _started
    if _started:
        return
    if os.environ.get("RUN_MAIN") != "true" and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        pass
    t = threading.Thread(target=_loop, name=_THREAD_NAME, daemon=True)
    t.start()
    _started = True
