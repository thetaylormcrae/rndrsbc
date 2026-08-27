"""
rndrSBC - Album Providers
============================================================================
Album providers abstract *where* photos come from. A photo frame zone can
source its gallery from any registered provider, keeping the render path
identical whether the image lives on local disk or at a remote URL.

Design goals
------------
* Local albums remain the zero-config default (fully backwards compatible).
* New sources are thin Provider subclasses (Google/Amazon/Microsoft/Nextcloud
  adapters can slot in without touching the core).
* Failure is graceful: a provider that can't reach its source yields an empty
  album (-> the widget's "no photos" empty-state) instead of crashing the frame.
"""

import io
import os
import time
import json
import logging
import urllib.request

from PIL import Image

logger = logging.getLogger("rndrSBC.providers")

# Global provider registry: name -> Provider subclass factory handle
_PROVIDERS = {}


def register_provider(name: str):
    """Class decorator registering ``cls`` as an album provider under ``name``."""
    def deco(cls):
        _PROVIDERS[name] = cls
        cls.provider_name = name
        return cls
    return deco


def available_providers():
    """Return {name: cls} of all registered album providers."""
    return dict(_PROVIDERS)


class AlbumProvider:
    """Base contract every album provider implements.

    An *album* is identified by ``album_id``. Photos are returned as metadata
    dicts carrying at least ``name`` and ``provider``. ``open_image`` returns a
    PIL image for a given photo metadata dict.
    """

    provider_name = "base"

    def list_albums(self) -> list:
        raise NotImplementedError

    def list_photos(self, album_id: str) -> list:
        raise NotImplementedError

    def open_image(self, photo: dict):
        """Return a PIL Image for the given photo metadata dict."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Provider 1 - Local disk (the original behavior)
# ---------------------------------------------------------------------------
@register_provider("local")
class LocalDiskProvider(AlbumProvider):
    """Photos stored under /data/photos[/album]. Zero-config default."""

    def __init__(self, data_dir: str = None):
        self._photo_dir = os.path.join(data_dir, "photos") if data_dir else None

    def _root(self) -> str:
        if self._photo_dir:
            return self._photo_dir
        from widgets.photo_frame.widget import PHOTO_DIR
        return PHOTO_DIR

    def list_albums(self) -> list:
        root = self._root()
        if not os.path.isdir(root):
            return []
        return [e for e in sorted(os.listdir(root))
                if os.path.isdir(os.path.join(root, e))]

    def list_photos(self, album_id: str = None) -> list:
        return list_local_photos(self._root(), album_id)

    def open_image(self, photo: dict):
        path = photo.get("path")
        if not path:
            raise FileNotFoundError("local photo missing path")
        with Image.open(path) as im:
            return im.convert("RGB")


def list_local_photos(root: str, album_id: str = None) -> list:
    ALLOWED = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif")
    if not os.path.isdir(root):
        return []
    if album_id and album_id != "default":
        folder = os.path.join(root, album_id)
        if not os.path.isdir(folder):
            return []
        return _scan(folder, album_id, ALLOWED)
    out = []
    for entry in sorted(os.listdir(root)):
        p = os.path.join(root, entry)
        if os.path.isdir(p):
            out.extend(_scan(p, entry, ALLOWED))
        elif entry.lower().endswith(ALLOWED):
            out.append(_scan_one(p, "default", ALLOWED))
    return out


def _scan(folder: str, album: str, allowed) -> list:
    return [_scan_one(os.path.join(folder, f), album, allowed)
            for f in sorted(os.listdir(folder)) if f.lower().endswith(allowed)]


def _scan_one(fpath: str, album: str, allowed) -> dict:
    try:
        with Image.open(fpath) as im:
            return {"name": os.path.basename(fpath), "path": fpath, "album": album,
                    "provider": "local", "width": im.width, "height": im.height,
                    "size": os.path.getsize(fpath)}
    except Exception:
        return {"name": os.path.basename(fpath), "path": fpath, "album": album,
                "provider": "local", "width": 0, "height": 0, "size": 0}


# ---------------------------------------------------------------------------
# Provider 2 - Remote HTTP(S) gallery
# ---------------------------------------------------------------------------
# A remote album is a stable base URL (a gallery endpoint, a NAS static dir,
# a Nextcloud/WebDAV folder listing, etc.). Photo discovery is either:
#   * an explicit ``?index=<url>`` = a JSON array of photo URLs/objects, or
#   * numeric auto-enumeration ``?enumerate=<start>-<end>`` for hosts exposing
#     deterministic names like /IMG_0001.jpg .. /IMG_0012.jpg, or
#   * a single image URL directly as the album.
# Images are fetched on render into a memory buffer, then treated exactly
# like a local file by the rendering pipeline.
@register_provider("http")
class HttpProvider(AlbumProvider):
    _TIMEOUT = 8.0

    def __init__(self, session_id=None):
        self._cache = {}
        self._cache_ts = 0

    # -- helpers ------------------------------------------------------------
    def _parse_album(self, album_id: str) -> dict:
        """Break an album spec into (base, index_url, enumerate, headers)."""
        spec = album_id
        headers = {}
        # optional leading queries are passed already inside album_id,
        # but support a `token=` for basic auth style usage
        base = spec
        index_url = None
        enum = None
        if "?index=" in spec:
            base, index_url = spec.split("?index=", 1)
        if "?enumerate=" in spec:
            base, enum = spec.split("?enumerate=", 1)
        return {"base": base.rstrip("/"), "index_url": index_url, "enumerate": enum,
                "headers": headers}

    def _http_get(self, url: str, timeout: float = None) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "rndrSBC/0.1"})
        with urllib.request.urlopen(req, timeout=timeout or self._TIMEOUT) as r:
            return r.read()

    def list_albums(self) -> list:
        # Remote providers expose albums explicitly; without credentials we
        # surface nothing here (zones configure their album URL directly).
        return []

    def list_photos(self, album_id: str) -> list:
        cfg = self._parse_album(album_id)
        photos = []
        if cfg["index_url"]:
            try:
                data = json.loads(self._http_get(cfg["index_url"]))
            except Exception as e:
                logger.warning(f"http: could not fetch index {cfg['index_url']}: {e}")
                return []
            for item in data if isinstance(data, list) else data.get("photos", []):
                if isinstance(item, str):
                    url = item
                else:
                    url = item.get("url") or item.get("src") or item.get("path")
                    name = item.get("name") or item.get("title")
                if not url:
                    continue
                full = url if url.startswith("http") else cfg["base"] + "/" + url.lstrip("/")
                photos.append({"name": name or full.rsplit("/", 1)[-1], "url": full,
                               "album": album_id, "provider": "http"})
            # also offer any sibling images if base lists them? no - keep to index
            return photos
        if cfg["enumerate"]:
            try:
                start, end = (int(x) for x in cfg["enumerate"].split("-"))
            except Exception:
                return []
            for i in range(start, end + 1):
                url = cfg["base"] + "/" + str(i)
                photos.append({"name": str(i), "url": url, "album": album_id, "provider": "http"})
            return photos
        # single-image album
        if cfg["base"]:
            return [{"name": cfg["base"].rsplit("/", 1)[-1], "url": cfg["base"],
                     "album": album_id, "provider": "http"}]
        return []

    def open_image(self, photo: dict):
        url = photo.get("url")
        if not url:
            raise FileNotFoundError("http photo missing url")
        key = f"{url}|{int(time.time() // 60)}"  # 60s in-memory cache
        if key not in self._cache:
            try:
                self._cache[key] = self._http_get(url)
            except Exception as e:
                self._cache.pop(key, None)
                raise IOError(f"http: could not fetch {url}: {e}")
            # prune stale entries, keep tiny
            if len(self._cache) > 8:
                keep = sorted(self._cache.items(), key=lambda kv: kv[0].rsplit("|", 1)[-1])[-8:]
                self._cache = dict(keep)
        img = Image.open(io.BytesIO(self._cache[key]))
        return img.convert("RGB")


# ---------------------------------------------------------------------------
# Provider 3 - Authorized (OAuth-protected) remote gallery
# ---------------------------------------------------------------------------
# Wraps any OAuth-protected image endpoint (Google Photos, Microsoft Photos,
# Amazon Photos, Nextcloud) behind the standard AlbumProvider interface.
#
# The album spec carries the vendor + a per-call fetch strategy:
#
#   provider:oauth:google            -> uses Google Photos library, default album
#   provider:oauth:google:<album>    -> named Google Photos album
#   provider:oauth:microsoft:<id>    -> Microsoft Graph drive item / folder id
#   provider:oauth:amazon:<nodeId>   -> Amazon Photos node
#
# Authentication is handled by core.oauth.OAuthClient (auto-refresh). The
# vendor's read API + image fetch are expressed via two small callables so
# adding a vendor never touches the core widget logic.
@register_provider("oauth")
class AuthorizedHttpProvider(AlbumProvider):
    def __init__(self, client=None, transport=None, credentials=None):
        from core import oauth as _oauth
        if client is None:
            # caller passes vendor via album spec; placeholder until resolve
            self._client = None
        else:
            self._client = client
        self.transport = transport or _oauth.HttpTransport()
        self.credentials = credentials or {}

    def _ensure_client(self, vendor: str):
        if self._client is None:
            from core import oauth as _oauth
            self._client = _oauth.vendored_client(vendor, self.credentials,
                                                  transport=self.transport)
        return self._client

    def list_photos(self, album_id: str) -> list:
        # album_id is ``<vendor>`` or ``<vendor>:<sub>``
        vendor, _, sub = album_id.partition(":")
        client = self._ensure_client(vendor)
        token = client.get_access_token()
        photos = self._fetch_photo_list(vendor, sub, token)
        return photos

    def open_image(self, photo: dict):
        url = photo.get("url")
        if not url:
            raise FileNotFoundError("authorized photo missing url")
        vendor, _, _ = (photo.get("album") or "").partition(":")
        client = self._ensure_client(vendor)
        token = client.get_access_token()
        if photo.get("download") or photo["url"]:
            r = self.transport.get(url, headers=self._auth(token))
            if r.status_code >= 400:
                raise IOError(f"authorized fetch {r.status_code} for {url}")
            try:
                with Image.open(io.BytesIO(r.content)) as im:
                    return im.convert("RGB")
            except Exception as e:
                raise IOError(f"bad image bytes from {url}: {e}")

    def _auth(self, token):
        return {"Authorization": f"Bearer {token}", "Accept": "image/*"}

    def _fetch_photo_list(self, vendor, sub, token):
        if vendor == "google":
            return _google_photos_list(self.transport, token, sub)
        if vendor == "microsoft":
            return _microsoft_photos_list(self.transport, token, sub)
        if vendor == "amazon":
            return _amazon_photos_list(self.transport, token, sub)
        # generic Nextcloud / others
        return _nextcloud_list(self.transport, token, sub)


# --- vendor-specific photo list fetchers (thin; JSON-in/JSON-out) ----------
def _google_photos_list(transport, token, sub):
    headers = {"Authorization": f"Bearer {token}"}
    url = "https://photoslibrary.googleapis.com/v1/mediaItems"
    params = [("pageSize", "100")]
    r = transport.get(url + "?" + "&".join(f"{k}={v}" for k, v in params),
                      headers=headers)
    if r.status_code >= 400:
        raise IOError(f"google photos {r.status_code}")
    items = r.json().get("mediaItems", [])
    out = []
    for it in items:
        out.append({
            "name": it.get("filename", it.get("id")),
            "id": it.get("id"),
            "url": it.get("baseUrl"),
            "download": it.get("baseUrl") + "=d",
            "album": "google:" + (sub or ""),
            "provider": "oauth",
        })
    return out


def _microsoft_photos_list(transport, token, sub):
    headers = {"Authorization": f"Bearer {token}"}
    q = "" if not sub else f"/{sub}"
    url = f"https://graph.microsoft.com/v1.0/me/drive/items{q}/children" if sub \
        else "https://graph.microsoft.com/v1.0/me/drive/special/photos/children"
    r = transport.get(url, headers=headers)
    if r.status_code >= 400:
        raise IOError(f"microsoft graph {r.status_code}")
    out = []
    for it in r.json().get("value", []):
        out.append({
            "name": it.get("name", it.get("id")),
            "id": it.get("id"),
            "url": it["content"].get("@microsoft.graph.downloadUrl") if it.get("content") else None,
            "album": "microsoft:" + (sub or ""),
            "provider": "oauth",
        })
    return out


def _amazon_photos_list(transport, token, sub):
    headers = {"Authorization": f"Bearer {token}"}
    url = "https://drive.amazonaws.com/drive/v1/nodes/" + (sub or "")
    r = transport.get(url, headers=headers)
    if r.status_code >= 400:
        raise IOError(f"amazon photos {r.status_code}")
    out = []
    for it in r.json().get("data", []):
        out.append({
            "name": it.get("name", it.get("id")),
            "id": it.get("id"),
            "url": it.get("kind") and (it.get("downloadUrl") or it.get("thumbnailUrl")),
            "album": "amazon:" + (sub or ""),
            "provider": "oauth",
        })
    return out


def _nextcloud_list(transport, token, sub):
    headers = {"Authorization": f"Bearer {token}"}
    url = "https://example.nextcloud/remote.php/dav/files/user" + (sub or "")
    # WebDAV-style listing — kept minimal; production instance supplies real URL
    r = transport.get(url, headers=headers)
    if r.status_code >= 400:
        raise IOError(f"nextcloud {r.status_code}")
    return []


def resolve_provider(album_spec: str = None) -> tuple:
    """Return (provider, album_id) for a zone ``album`` setting.

    * ``None`` / ``default`` / plain name  -> local provider, that album name
    * ``remote/http:<...>``                -> HttpProvider, spec after the colon
    * ``provider:oauth:<vendor>[:<sub>]``  -> AuthorizedHttpProvider (OAuth)
    * ``remote/http:...``                  -> HttpProvider (alias)
    """
    if not album_spec or album_spec.lower() == "default":
        return LocalDiskProvider(), None
    if album_spec.startswith("provider:oauth:"):
        inner = album_spec[len("provider:oauth:"):]
        return AuthorizedHttpProvider(), inner
    if album_spec.startswith("remote/http:"):
        return HttpProvider(), album_spec[len("remote/http:"):]
    if album_spec.startswith("provider:"):
        rest = album_spec[len("provider:"):]
        name, _, album_id = rest.partition(":")
        cls = _PROVIDERS.get(name)
        if not cls:
            raise ValueError(f"unknown provider '{name}'")
        try:
            return cls(), (album_id or None)
        except TypeError:
            return cls, (album_id or None)
    return LocalDiskProvider(), album_spec


def list_album_photos(provider: AlbumProvider, album_id: str) -> list:
    return provider.list_photos(album_id)


def open_image(provider: AlbumProvider, photo: dict):
    return provider.open_image(photo)