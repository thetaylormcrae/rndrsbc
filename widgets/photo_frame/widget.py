"""
rndrSBC - Photo Frame Widget
Turns the e-Paper display into a rotating digital photo frame.
Photos are uploaded through the dashboard and stored in /data/photos.
Renders each photo with smart scaling (cover-fit) + optional caption.
"""

import os
import json
import logging
import random
import time
from PIL import Image, ImageDraw, ImageOps, ImageEnhance

from core.canvas import ResponsiveCanvas, Rect
from core.providers import resolve_provider, open_image, list_album_photos
from widgets.base import BaseWidget, register_widget

logger = logging.getLogger("rndrSBC.photo_frame")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
PHOTO_DIR = os.path.join(DATA_DIR, "photos")
INDEX_FILE = os.path.join(DATA_DIR, "photo_index.json")
CACHE_DIR = os.path.join(DATA_DIR, "photos_cache")

ALLOWED_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif")


def list_photos(album: str = None) -> list:
    """Returns metadata for all photos in gallery ``album`` (or all albums).

    Albums map to subdirectories of ``/data/photos/<album_name>/`` so a single
    frame can host independent galleries (e.g. "family", "travel") that rotate
    separately on different zones. ``album=None`` scans every album.
    """
    if not os.path.isdir(PHOTO_DIR):
        return []
    if album:
        album_dir = os.path.join(PHOTO_DIR, album)
        if not os.path.isdir(album_dir):
            return []
        return _scan_dir(album_dir, album)
    photos = []
    for entry in sorted(os.listdir(PHOTO_DIR)):
        p = os.path.join(PHOTO_DIR, entry)
        if os.path.isdir(p):
            photos.extend(_scan_dir(p, entry))
        elif entry.lower().endswith(ALLOWED_EXT):
            photos.append(_scan_one(p, album="default"))
    return photos


def list_albums() -> list:
    """Return names of all galleries (albums) on the frame."""
    if not os.path.isdir(PHOTO_DIR):
        return []
    albums = []
    for entry in sorted(os.listdir(PHOTO_DIR)):
        p = os.path.join(PHOTO_DIR, entry)
        if os.path.isdir(p):
            albums.append(entry)
    return albums


def _scan_dir(folder: str, album: str) -> list:
    out = []
    for fname in sorted(os.listdir(folder)):
        if fname.lower().endswith(ALLOWED_EXT):
            out.append(_scan_one(os.path.join(folder, fname), album))
    return out


def _scan_one(fpath: str, album: str) -> dict:
    try:
        with Image.open(fpath) as im:
            return {
                "name": os.path.basename(fpath),
                "path": fpath,
                "album": album,
                "size": os.path.getsize(fpath),
                "width": im.width,
                "height": im.height,
            }
    except Exception:
        return {"name": os.path.basename(fpath), "path": fpath, "album": album, "size": os.path.getsize(fpath), "width": 0, "height": 0}


def _resolve_album_path(album: str) -> str:
    """Returns the directory for a named album, creating it if needed."""
    album_dir = os.path.join(PHOTO_DIR, album) if album else PHOTO_DIR
    os.makedirs(album_dir, exist_ok=True)
    return album_dir


def save_photo(data: bytes, filename: str, album: str = "default") -> str:
    """Saves an uploaded photo to gallery ``album`` (default: the root library)."""
    album_dir = _resolve_album_path(album)
    safe_name = os.path.basename(filename).replace(" ", "_")
    fpath = os.path.join(album_dir, safe_name)
    with open(fpath, "wb") as f:
        f.write(data)
    # Normalize orientation & convert to a palette-friendly size
    try:
        with Image.open(fpath) as im:
            fixed = ImageOps.exif_transpose(im)
            fixed = fixed.convert("RGB")
            fixed.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            fixed.save(fpath, quality=88)
    except Exception as e:
        logger.warning(f"Could not normalize photo {filename}: {e}")
    return fpath


def _load_index() -> dict:
    if os.path.exists(INDEX_FILE):
        try:
            return json.load(open(INDEX_FILE))
        except Exception:
            return {}
    return {}


def _save_index(idx: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(INDEX_FILE, "w") as f:
        json.dump(idx, f, indent=2)


@register_widget("photo_frame", "Photo Frame")
class PhotoFrameWidget(BaseWidget):
    """Displays uploaded photos with timing and cover-fit rendering."""

    name = "Photo Frame"
    description = "Rotate personal photos on the display"
    default_interval_minutes = 5

    def get_config_schema(self) -> dict:
        return {
            "fields": [
                {"name": "caption", "label": "Show Caption", "type": "boolean", "default": True},
                {"name": "mode", "label": "Selection Mode", "type": "select", "options": ["sequential", "random"], "default": "sequential"},
                {"name": "album", "label": "Gallery (Album)", "type": "text", "default": "default", "help": "Local folder under /data/photos, or a remote gallery: remote/http:<base>?index=<url> / ?enumerate=<s>-<e> / provider:<name>:<album>"},
            ]
        }

    def _pick_photo(self, photos, settings, index):
        """Choose the next photo to show given selected mode + album (per-gallery index key)."""
        last_name = index.get("last_photo")
        candidates = [p for p in photos if p["name"] != last_name] or photos
        if settings.get("mode", "sequential") == "random":
            return random.choice(candidates)
        # Sequential: pick next after last
        names = [p["name"] for p in photos]
        try:
            pos = names.index(last_name) + 1
            return photos[pos % len(photos)]
        except ValueError:
            return photos[0]

    @staticmethod
    def _cache_path(photo: dict, width: int, height: int) -> str:
        """Disk-level pre-rendered 1-bit frame cache path (low-RAM friendly)."""
        key = photo.get("path") or photo.get("url") or ""
        digest = key.replace(os.sep, "_").replace(".", "")[:80]
        return os.path.join(CACHE_DIR, f"{digest}_{width}x{height}.bmp")

    def _render_frame(self, provider, photo: dict, dimensions, settings, bounds):
        """Render a single photo as a frame, honoring an on-disk cache."""
        with ResponsiveCanvas(dimensions, bg_color="#000000") as canvas:
            content = bounds if bounds is not None else canvas.bounds

            # Cache keyed on photo + target box so re-renders are O(copy).
            cache_path = self._cache_path(photo, content.w, content.h)
            if os.path.exists(cache_path):
                try:
                    with Image.open(cache_path) as cached:
                        if cached.size == (content.w, content.h):
                            canvas.image.paste(cached.convert("1"), (content.x, content.y))
                            return canvas.to_image()
                except Exception:
                    pass

            src = open_image(provider, photo)  # provider-aware fetch (disk or remote)

            target_w, target_h = content.w, content.h
            if settings.get("caption", True):
                target_h = max(60, content.h - canvas.pt(44))

            # Cover-fit scale
            scale = max(target_w / max(1, src.width), target_h / max(1, src.height))
            new_w = max(1, int(src.width * scale))
            new_h = max(1, int(src.height * scale))
            src = src.resize((new_w, new_h), Image.Resampling.LANCZOS)

            left = (new_w - target_w) // 2
            top = (new_h - target_h) // 2
            src = src.crop((left, top, left + target_w, top + target_h))
            src = ImageEnhance.Contrast(src).enhance(1.1)

            canvas.image.paste(src, (content.x, content.y))

            if settings.get("caption", True):
                cap_font = canvas.get_token_font("caption")
                canvas.draw_text(photo["name"], (content.x + 8, content.bottom - canvas.pt(28)),
                                 font=cap_font, fill="#333333")


            # Persist a 1-bit cache copy for future low-RAM ticks.
            try:
                os.makedirs(CACHE_DIR, exist_ok=True)
                canvas.to_image().convert("1").save(cache_path)
            except Exception as e:
                logger.debug(f"cache write skipped: {e}")

            return canvas.to_image()

    def render(self, dimensions: tuple[int, int], settings: dict, bounds: Rect = None) -> Image.Image:
        album = (settings.get("album") or "default").strip()
        provider, album_id = resolve_provider(album)
        photos = list_album_photos(provider, album_id)
        if not photos:
            with ResponsiveCanvas(dimensions, bg_color="#ffffff") as canvas:
                content = bounds if bounds is not None else canvas.bounds
                canvas.draw_text(
                    f"No photos in gallery '{album}'.",
                    (content.x + content.w // 2, content.y + content.h // 2 - canvas.pt(16)),
                    font=canvas.get_token_font("body"), fill="#666666", anchor="mm")
                canvas.draw_text(
                    "Upload via the dashboard → Photos tab.",
                    (content.x + content.w // 2, content.y + content.h // 2 + canvas.pt(16)),
                    font=canvas.get_token_font("caption"), fill="#999999", anchor="mm")
                return canvas.to_image()

        # Per-gallery selection cursor so independent albums advance separately.
        index = _load_index()
        key = f"photo_frame::{album}"
        index.setdefault(key, {})
        gallery_idx = index[key]
        chosen = self._pick_photo(photos, settings, gallery_idx)
        gallery_idx["last_photo"] = chosen["name"]
        _save_index(index)

        return self._render_frame(provider, chosen, dimensions, settings, bounds)
