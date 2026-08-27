"""Guards for the benchmark harness (#4) and the doctor's config.schema (#2)."""
import os, sys

import pytest

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_bench_harness_renders_clock():
    """The benchmark must produce a mode_ok report for a real widget, else the
    CI performance gate silently no-ops."""
    sys.path.insert(0, root)
    from bench import bench_render
    rep = bench_render.bench("clock", 400, 300, 5)  # small/fast for unit runtime
    assert rep["mode_ok"] is True
    assert rep["iterations"] == 5
    assert rep["render_ms"]["p50"] > 0


def test_bench_harness_rejects_missing_widget():
    sys.path.insert(0, root)
    from bench import bench_render
    with pytest.raises((RuntimeError, ModuleNotFoundError)):
        bench_render._load_widget("does_not_exist")


def test_doctor_config_schema_check_present():
    """doctor must expose the config.schema verdict wired in for #2."""
    sys.path.insert(0, root)
    import rndrsbc.doctor as d
    checks = list(d.check_config())
    names = {c[0] for c in checks}
    assert "config.schema" in names
    schema = [c for c in checks if c[0] == "config.schema"][0]
    assert schema[2] in ("ok", "warn", "fail", "skip")
