"""
rndrSBC - Abstract Widget Interface & Plugin Architecture
Self-registering widget framework with async data caching, error isolation, and timezone awareness.

Key Developer Features:
- `@register_widget("name")` decorator for zero-config auto-discovery.
- `self.fetch_remote_json()` managed background caching with graceful stale-data fallback.
- `safe_render()` error isolation: renders a clean inline visual fallback on exceptions.
- `self.get_local_now()` native Python `zoneinfo` support for exact DST & timezone awareness.
"""

from abc import ABC, abstractmethod
from PIL import Image
import os
import sys
import time
import json
import logging
import threading
import importlib
import traceback
from datetime import datetime
import zoneinfo
import requests

from core.canvas import ResponsiveCanvas, Rect

logger = logging.getLogger("rndrSBC.widgets")

# Global Registry of Discovered Widgets
WIDGET_REGISTRY: dict[str, "BaseWidget"] = {}

# Thread-safe global HTTP cache: {url: {"data": dict, "ts": float, "fetching": bool, "error": str}}
_CACHE_LOCK = threading.Lock()
_REMOTE_CACHE: dict[str, dict] = {}


def register_widget(name: str, display_name: str = None):
    """Decorator to automatically register a widget class into the global registry."""
    def decorator(cls):
        instance = cls()
        if display_name:
            instance.name = display_name
        WIDGET_REGISTRY[name] = instance
        logger.info(f"Registered widget plugin: '{name}' ({instance.name})")
        return cls
    return decorator


def _scan_dir(base_module: str, scan_dir: str) -> None:
    """Import every `<scan_dir>/<entry>/widget.py` under `base_module` ns.

    ``scan_dir`` is expected to be a directory of widget folders; the parent of
    ``scan_dir`` is added to ``sys.path`` so ``import {base_module}...`` works
    whether ``scan_dir`` is the bundled ``widgets/`` (parent already importable)
    or the deployment ``plugins`` dir (parent normally not on ``sys.path``).
    """
    parent = os.path.dirname(scan_dir) if scan_dir else None
    if parent and parent not in sys.path:
        sys.path.insert(0, parent)
    if not os.path.isdir(scan_dir):
        return
    for entry in os.listdir(scan_dir):
        full_p = os.path.join(scan_dir, entry)
        if os.path.isdir(full_p) and not entry.startswith("_"):
            widget_file = os.path.join(full_p, "widget.py")
            if os.path.exists(widget_file):
                module_name = f"{base_module}.{entry}.widget"
                try:
                    if module_name in sys.modules:
                        importlib.reload(sys.modules[module_name])
                    else:
                        importlib.import_module(module_name)
                    logger.debug(f"Loaded widget module: {module_name}")
                except Exception as e:
                    logger.error(f"Failed to load widget module '{module_name}': {e}\n{traceback.format_exc()}")


def discover_widgets(widgets_dir: str = None):
    """
    Auto-discovers and imports all widget plugins.

    Scans BOTH the bundled ``widgets/`` package AND the deployment ``plugins/``
    dir (RNDRSBC_HOME/plugins) so community widgets installed via
    :mod:`core.registry` are picked up on the next render cycle without a
    restart.
    """
    if widgets_dir is None:
        widgets_dir = os.path.dirname(__file__)

    logger.info(f"Discovering widgets in {widgets_dir}...")
    _scan_dir("widgets", widgets_dir)

    # Deployment-owned community plugins (git-free, via the registry).
    try:
        from core import paths as _paths
        _scan_dir("plugins", _paths.PLUGIN_DIR)
    except Exception as e:
        logger.warning(f"Community plugin scan skipped: {e}")

    return WIDGET_REGISTRY


class BaseWidget(ABC):
    """Abstract base class for all rndrSBC screen widgets."""
    name: str = "Base Widget"
    description: str = ""
    default_interval_minutes: int = 15

    # Responsive-layout contract. Widgets that cannot render below a certain
    # size declare it here so the grid compositor can hide / shrink them.
    min_dimensions: tuple[int, int] = (0, 0)   # (min_width, min_height), 0 = any

    @abstractmethod
    def render(self, dimensions: tuple[int, int], settings: dict, bounds: Rect = None) -> Image.Image:
        """Draws the widget on a ResponsiveCanvas and returns the PIL Image."""
        pass

    @abstractmethod
    def get_config_schema(self) -> dict:
        """Returns metadata for the web dashboard settings UI."""
        pass

    def get_local_now(self, tz_str: str = None, settings: dict = None) -> datetime:
        """
        Returns accurate timezone-aware datetime using Python's native `zoneinfo`.
        Handles daylight saving time shifts without third-party dependencies.
        """
        tz_name = tz_str
        if not tz_name and settings:
            tz_name = settings.get("timezone")

        if not tz_name:
            # Check global config if available
            try:
                config_path = os.path.join(os.path.dirname(__file__), "..", "config.json")
                if os.path.exists(config_path):
                    with open(config_path, "r") as f:
                        cfg = json.load(f)
                        tz_name = cfg.get("device", {}).get("timezone")
            except Exception:
                pass

        if not tz_name:
            tz_name = "UTC"

        try:
            return datetime.now(zoneinfo.ZoneInfo(tz_name))
        except Exception:
            logger.warning(f"Invalid timezone '{tz_name}', falling back to UTC")
            return datetime.now(zoneinfo.ZoneInfo("UTC"))

    def fetch_remote_json(self, url: str, ttl: int = 300, headers: dict = None,
                          params: dict = None, default: dict = None, timeout: int = 10) -> tuple[dict, bool]:
        """
        Thread-safe asynchronous HTTP caching helper with graceful stale degradation.
        
        Returns:
            tuple[dict, bool]: (json_data, is_stale)
            - If cache is valid: returns (cached_data, False)
            - If cache is expired but exists: returns (cached_data, True) and starts background fetch
            - If no cache exists: fetches synchronously with timeout
        """
        cache_key = url
        if params:
            cache_key += "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))

        now = time.time()
        cached = None

        with _CACHE_LOCK:
            if cache_key in _REMOTE_CACHE:
                cached = _REMOTE_CACHE[cache_key]

        # Case 1: Valid unexpired cache
        if cached and (now - cached.get("ts", 0)) < ttl:
            return cached.get("data", default or {}), False

        # Case 2: Stale cache exists -> return stale data immediately and refresh in background
        if cached and "data" in cached:
            if not cached.get("fetching", False):
                with _CACHE_LOCK:
                    _REMOTE_CACHE[cache_key]["fetching"] = True

                def _bg_fetch():
                    try:
                        resp = requests.get(url, headers=headers, params=params, timeout=timeout)
                        resp.raise_for_status()
                        data = resp.json()
                        with _CACHE_LOCK:
                            _REMOTE_CACHE[cache_key] = {
                                "data": data,
                                "ts": time.time(),
                                "fetching": False,
                                "error": None
                            }
                        logger.debug(f"[Async Cache] Successfully refreshed: {url}")
                    except Exception as err:
                        with _CACHE_LOCK:
                            if cache_key in _REMOTE_CACHE:
                                _REMOTE_CACHE[cache_key]["fetching"] = False
                                _REMOTE_CACHE[cache_key]["error"] = str(err)
                        logger.warning(f"[Async Cache] Background refresh failed for {url}: {err}")

                threading.Thread(target=_bg_fetch, daemon=True, name=f"bg-fetch-{self.name}").start()

            return cached.get("data", default or {}), True

        # Case 3: No cache exists -> synchronous fetch
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            with _CACHE_LOCK:
                _REMOTE_CACHE[cache_key] = {
                    "data": data,
                    "ts": time.time(),
                    "fetching": False,
                    "error": None
                }
            return data, False
        except Exception as e:
            logger.error(f"[HTTP Fetch] Synchronous request failed for {url}: {e}")
            if default is not None:
                return default, True
            raise e

    def safe_render(self, dimensions: tuple[int, int], settings: dict, bounds: Rect = None) -> Image.Image:
        """
        Guards widget execution against unhandled exceptions and missing dependencies.
        Renders an informative inline error card instead of halting the system render loop.
        """
        try:
            return self.render(dimensions, settings, bounds=bounds)
        except Exception as e:
            logger.error(f"[Widget Error] '{self.name}' render failed: {e}\n{traceback.format_exc()}")
            return self._render_error_card(dimensions, str(e), traceback.format_exc())

    def _render_error_card(self, dimensions: tuple[int, int], error_msg: str, tb_str: str) -> Image.Image:
        """Draws a clean, resolution-adaptive visual error card."""
        with ResponsiveCanvas(dimensions, bg_color="#ffffff") as canvas:
            box = canvas.bounds.inset(canvas.pt(20))
            canvas.draw_card(box, radius=8, fill="#ffffff", outline="#e65c00", width=2)
            
            h_box, content_box = box.split_rows([2.0, 8.0], gap=canvas.pt(8))
            
            font_title = canvas.get_token_font("headline")
            font_body = canvas.get_token_font("body")
            font_code = canvas.get_token_font("caption")

            # Header
            canvas.draw_text(f"⚠️ Widget Error: {self.name}", (h_box.x + canvas.pt(16), h_box.y + canvas.pt(12)),
                             font=font_title, fill="#e65c00")
            
            # Message
            canvas.draw_text(f"Exception: {error_msg}", (content_box.x + canvas.pt(16), content_box.y + canvas.pt(8)),
                             font=font_body, fill="#000000")
            
            # Traceback snippet
            lines = [l for l in tb_str.strip().split("\n")[-3:]]
            tb_snippet = " | ".join(lines)
            if len(tb_snippet) > 90:
                tb_snippet = tb_snippet[:87] + "..."
            canvas.draw_text(tb_snippet, (content_box.x + canvas.pt(16), content_box.y + canvas.pt(36)),
                             font=font_code, fill="#666666")

            canvas.draw_frame("Corner", color="#e65c00")
            return canvas.to_image()


# --- Responsive-layout helpers for the grid compositor ----------------------

def min_size_for(widget) -> tuple[int, int]:
    """Return the minimum (width, height) a widget needs to render meaningfully.

    Reads the widget's ``min_dimensions`` class attribute; falls back to (0, 0)
    meaning "renders at any size" for widgets that don't declare one.
    """
    md = getattr(widget, "min_dimensions", (0, 0)) or (0, 0)
    try:
        return (int(md[0]), int(md[1]))
    except Exception:  # noqa: BLE001
        return (0, 0)


def fits_zone(widget, zone_w: int, zone_h: int) -> bool:
    """True if the widget can render meaningfully inside a zone box."""
    mw, mh = min_size_for(widget)
    if mw <= 0 or mh <= 0:
        return True
    return zone_w >= mw and zone_h >= mh


def nearest_best_widget(allowed_names, zone_w: int, zone_h: int):
    """Pick the first widget in ``allowed_names`` that fits a zone; None if none fit."""
    for name in allowed_names:
        w = WIDGET_REGISTRY.get(name)
        if w is not None and fits_zone(w, zone_w, zone_h):
            return w
    return None


