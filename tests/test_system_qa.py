import os
import sys
import tempfile
import shutil
from PIL import Image

sys.path.insert(0, '.')

def test_1_widgets():
    print("Test 1: Discovering and rendering all widgets...")
    from widgets.base import discover_widgets, WIDGET_REGISTRY
    discover_widgets()
    print(f"  Discovered widgets: {list(WIDGET_REGISTRY.keys())}")
    assert "onboarding" in WIDGET_REGISTRY
    assert "clock" in WIDGET_REGISTRY
    assert "network" in WIDGET_REGISTRY
    assert "system_stats" in WIDGET_REGISTRY
    assert "weather" in WIDGET_REGISTRY
    assert "calendar" in WIDGET_REGISTRY
    assert "photo_frame" in WIDGET_REGISTRY

    for widget_id, w in WIDGET_REGISTRY.items():
        img = w.render((800, 480), {})
        assert isinstance(img, Image.Image), f"{widget_id} did not return a PIL Image"
        assert img.size == (800, 480), f"{widget_id} rendered wrong size: {img.size}"
        print(f"  [PASS] {widget_id} rendered ({img.size[0]}x{img.size[1]})")

def test_2_paths():
    print("\nTest 2: Testing paths and relocatable deployment home...")
    from core import paths
    preview = paths.resolve("live_screen.png")
    assert preview.endswith("live_screen.png")
    assert os.path.isabs(preview)
    print(f"  [PASS] resolve('live_screen.png') -> {preview}")

    # Test with custom RNDRSBC_HOME in temp dir
    with tempfile.TemporaryDirectory() as tmpdir:
        old_env = os.environ.get("RNDRSBC_HOME")
        try:
            os.environ["RNDRSBC_HOME"] = tmpdir
            # Reload deployment root
            import importlib
            importlib.reload(paths)
            assert paths.DEPLOY_ROOT == tmpdir
            assert paths.CONFIG_PATH == os.path.join(tmpdir, "config.json")
            assert paths.resolve("live_screen.png") == os.path.join(tmpdir, "live_screen.png")
            print(f"  [PASS] Custom RNDRSBC_HOME reanchored correctly to {tmpdir}")
        finally:
            if old_env is not None:
                os.environ["RNDRSBC_HOME"] = old_env
            else:
                os.environ.pop("RNDRSBC_HOME", None)
            importlib.reload(paths)

def test_3_virtual_display():
    print("\nTest 3: Testing VirtualDisplay output path anchoring...")
    with tempfile.TemporaryDirectory() as tmpdir:
        from displays.virtual import VirtualDisplay
        out_file = os.path.join(tmpdir, "test_preview.png")
        vd = VirtualDisplay(width=800, height=480, output_path=out_file)
        test_img = Image.new("RGB", (800, 480), color="white")
        vd.update(test_img)
        assert os.path.exists(out_file)
        assert os.path.getsize(out_file) > 0
        print(f"  [PASS] VirtualDisplay wrote {os.path.getsize(out_file)} bytes to {out_file}")

        # Test bare filename auto-anchoring
        vd_bare = VirtualDisplay(width=800, height=480, output_path="preview_bare.png")
        assert os.path.isabs(vd_bare.output_path)
        print(f"  [PASS] Bare filename auto-anchored to: {vd_bare.output_path}")

def test_4_driver_resolution():
    print("\nTest 4: Testing driver resolution logic...")
    from core.qa import resolve_display
    
    # Named virtual
    d_virt = resolve_display({"display": {"driver": "virtual", "width": 800, "height": 480}})
    assert os.path.isabs(d_virt.output_path)
    print(f"  [PASS] Named virtual driver resolved cleanly: {d_virt.output_path}")

    # Auto fallback
    d_auto = resolve_display({"display": {"driver": "auto", "width": 800, "height": 480}})
    assert d_auto is not None
    print(f"  [PASS] Auto driver resolved cleanly: {type(d_auto).__name__}")

def test_5_onboarding_qr():
    print("\nTest 5: Testing onboarding claim token issuance and URL generation...")
    from server.onboarding import issue_claim_token, validate_claim_token, claim_url_for_token
    import qrcode

    token_data = issue_claim_token(ttl_secs=300)
    tok = token_data["token"]
    assert validate_claim_token(tok) is True
    assert validate_claim_token("invalid_token") is False
    print(f"  [PASS] Claim token generated and validated: {tok[:8]}...")

    url = claim_url_for_token(tok)
    assert tok in url
    print(f"  [PASS] Claim URL: {url}")

    qr = qrcode.QRCode(box_size=10, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    assert isinstance(qr_img, Image.Image)
    assert qr_img.size[0] > 100
    print(f"  [PASS] QR image generated ({qr_img.size[0]}x{qr_img.size[1]})")

def test_6_buttons_controller():
    print("\nTest 6: Testing buttons pin configuration & dispatch...")
    from core.buttons import ButtonController
    pins = ButtonController.DEFAULT_PINS
    print(f"  Default pins: {pins}")
    assert pins["next"] == 5
    assert pins["prev"] == 6
    assert pins["toggle"] == 12
    # Ensure no collision with Pimoroni Inky pins (17, 27, 22)
    assert 17 not in pins.values()
    assert 27 not in pins.values()
    assert 22 not in pins.values()
    print("  [PASS] Buttons mapped cleanly to safe non-conflicting pins (5, 6, 12)")

def test_7_fresh_boot_clean():
    print("\nTest 7: Testing fresh boot configuration & zero-warning contract...")
    from main import fresh_default_config, load_config
    from core.config_schema import validate_config
    
    cfg = fresh_default_config()
    validated_cfg, warnings = validate_config(cfg)
    assert len(warnings) == 0, f"Warnings in fresh_default_config: {warnings}"
    assert validated_cfg["active_playlist"] == "setup"
    assert len(validated_cfg["playlists"]["setup"]["items"]) == 1
    assert validated_cfg["playlists"]["setup"]["items"][0]["widget"] == "onboarding"
    print("  [PASS] fresh_default_config() is onboarding-only with 0 warnings")

if __name__ == "__main__":
    test_1_widgets()
    test_2_paths()
    test_3_virtual_display()
    test_4_driver_resolution()
    test_5_onboarding_qr()
    test_6_buttons_controller()
    test_7_fresh_boot_clean()
    print("\n=======================================================")
    print(">>> COMPREHENSIVE QA TEST SUITE: 100% PASSED (7/7) <<<")
    print("=======================================================")
