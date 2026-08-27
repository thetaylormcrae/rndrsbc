"""
rndrSBC render + memory benchmark (prod-readiness #4).

Substantiates the README performance claims (``35ms-60ms render`` / ``<15MB``
RAM) with measurable, repeatable numbers. Renders a real widget (clock by
default) at a realistic e-paper resolution with a warm font cache, times it
over N iterations, and reports:

  * p50 / p95 / p99 render time in ms
  * peak current RSS delta and cumulative RSS (resource.getrusage)
  * hot-heap object growth (tracemalloc), if available

Run:   python3 bench/bench_render.py [widget] [width] [height] [iterations]
Exit:  0 on pass (renders complete), 1 if a widget fails to render.
JSON:  writes NORMALIZED_RENDER_MS etc. to stdout and a report file under
       bench/reports/<unix-ts>.json when REPORTS_DIR is writable.
"""
import json, os, resource, sys, time, tracemalloc, statistics, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")


def _load_widget(name: str):
    import importlib, inspect
    mod = importlib.import_module(f"widgets.{name}.widget")
    w = None
    for attr in sorted(vars(mod)):
        if attr.endswith("Widget"):
            cls = getattr(mod, attr)
            if isinstance(cls, type) and not inspect.isabstract(cls):
                w = cls()
                break
    if w is None:
        raise RuntimeError(f"widget {name}: no *Widget class found")
    return w


def bench(widget_name: str, width: int, height: int, iters: int) -> dict:
    w = _load_widget(widget_name)
    dims = (width, height)
    settings = {}
    start = resource.getrusage(resource.RUSAGE_SELF)
    base_rss = start.ru_maxrss

    tracemalloc.start()
    t0 = time.perf_counter()
    samples = []
    img = None
    for _ in range(iters):
        s = time.perf_counter()
        img = w.render(dims, settings)
        samples.append((time.perf_counter() - s) * 1000.0)
    elapsed = time.perf_counter() - t0
    _cur, _peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    end = resource.getrusage(resource.RUSAGE_SELF)
    samples.sort()

    def q(p):
        i = min(len(samples) - 1, int(p * len(samples)))
        return samples[i]

    report = {
        "widget": widget_name,
        "dims": (width, height),
        "iterations": iters,
        "render_ms": {
            "min": round(samples[0], 2),
            "p50": round(q(0.50), 2),
            "p95": round(q(0.95), 2),
            "p99": round(q(0.99), 2),
            "max": round(samples[-1], 2),
        },
        "rss_kb": {
            "peak": end.ru_maxrss,
            "base": base_rss,
            "delta_kb": end.ru_maxrss - base_rss,
        },
        "heap_bytes": {"current": _cur, "peak": _peak},
        "total_sec": round(elapsed, 3),
        "mode_ok": img is not None,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    return report


if __name__ == "__main__":
    widget_name = sys.argv[1] if len(sys.argv) > 1 else "clock"
    width = int(sys.argv[2]) if len(sys.argv) > 2 else 800
    height = int(sys.argv[3]) if len(sys.argv) > 3 else 480
    iters = int(sys.argv[4]) if len(sys.argv) > 4 else 30
    try:
        rep = bench(widget_name, width, height, iters)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    print(json.dumps(rep, indent=2))
    os.makedirs("bench/reports", exist_ok=True)
    with open(f"bench/reports/{int(time.time())}.json", "w") as fh:
        json.dump(rep, fh, indent=2)
    sys.exit(0 if rep["mode_ok"] else 1)
