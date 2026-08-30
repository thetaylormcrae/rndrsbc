"""Tests for core.panel_verify - SPI serialization + stall detection + auto-retry."""
import sys, os, threading, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.panel_verify import PanelVerify


def test_normal_push_no_retry():
    pv = PanelVerify()
    calls = []
    def push():
        calls.append(1)
    r = pv.run(push)
    assert r.ok and not r.retried
    assert len(calls) == 1


def test_raising_push_stalls_and_retries_once():
    pv = PanelVerify()
    calls = []
    def push():
        calls.append(1)
        raise RuntimeError("spi drop")
    r = pv.run(push)
    assert not r.ok and r.retried
    assert len(calls) == 2          # original + one auto-retry
    assert "retry_failed" in r.problems


def test_retry_succeeds_second_attempt():
    pv = PanelVerify()
    calls = []
    def push():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("busy")
    r = pv.run(push)
    assert r.ok and r.retried
    assert len(calls) == 2
    assert r.problems == []


def test_concurrent_pushes_are_serialized():
    pv = PanelVerify()
    active, max_active, total = 0, 0, 0
    guard = threading.Lock()
    def push():
        nonlocal active, max_active, total
        with guard:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.01)
        with guard:
            active -= 1
            total += 1
    threads = [threading.Thread(target=lambda: pv.run(push)) for _ in range(6)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert max_active == 1          # never overlapped on the "bus"
    assert total == 6
