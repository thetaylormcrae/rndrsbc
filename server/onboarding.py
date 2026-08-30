"""
rndrSBC - Onboarding & Provisioning Manager
Implements:
  #5  QR claim-token flow (connected case):   Pi advertises a one-time claim URL via QR;
       phone scans, lands on dashboard, claims device, completes first-run setup.
  #4  Automatic AP fallback / recovery mode:   If Wi-Fi is missing OR connection drops,
       Pi brings up a temporary Software AP and serves a captive provisioning portal.
       It re-enters AP mode automatically when the primary network is lost (recovery).
"""

import os
import re
import json
import time
import secrets
import logging
import threading
import subprocess

logger = logging.getLogger("rndrSBC.onboarding")

# ---------------------------------------------------------------------------
# QoS / configuration paths
#
# IMPORTANT: use the canonical resolver (core.paths), NOT this file's ``__file__``.
# In a PyPI/site-installed deployment ``__file__`` sits inside the read-only
# site-packages tree, so a relative path points at a phantom config that the
# boot path (core.paths.CONFIG_PATH -> $RNDRSBC_HOME/config.json) never reads.
# The onboarding-first default made the QR flow the first thing a fresh boot
# runs, so a divergent path here silently separated onboarding state from the
# real runtime config.
# ---------------------------------------------------------------------------
from core.paths import CONFIG_PATH, CLAIM_TOKEN_PATH  # noqa: E402

# Back-compat aliases (external callers may import the old names).
_CLAIM_TOKEN_PATH = CLAIM_TOKEN_PATH
_CLAIM_TOKEN_TTL_SECS = 60 * 60  # 1 hour

# In-memory cache to avoid re-reading state file every request
_claim_token_cache: dict = {}
_claim_lock = threading.Lock()


def _read_config() -> dict:
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_config(cfg: dict):
    tmp_path = CONFIG_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp_path, CONFIG_PATH)


def _read_claim_state() -> dict:
    """Loads persisted claim state from `.claim_token` (avoids leaking via config)."""
    with _claim_lock:
        if _claim_token_cache.get("loaded_ts", 0) + 5 > time.time():
            return _claim_token_cache

        if os.path.exists(_CLAIM_TOKEN_PATH):
            try:
                with open(_CLAIM_TOKEN_PATH, "r") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        else:
            data = {}
        _claim_token_cache.update(data)
        _claim_token_cache["loaded_ts"] = time.time()
        return _claim_token_cache


def _write_claim_state(state: dict):
    with _claim_lock:
        # Snapshot BEFORE clearing: `state` may BE the cache dict itself
        snapshot = dict(state)
        _claim_token_cache.clear()
        _claim_token_cache.update(snapshot)
        _claim_token_cache["loaded_ts"] = time.time()
        tmp_path = _CLAIM_TOKEN_PATH + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(snapshot, f, indent=2)
        os.replace(tmp_path, _CLAIM_TOKEN_PATH)


def issue_claim_token(ttl_secs: int = _CLAIM_TOKEN_TTL_SECS) -> dict:
    """
    Issues a fresh one-time claim token with an embedded expiry.
    Returns the token state: {token, claimed, created_at, expires_at}.
    """
    state = _read_claim_state()
    state["token"] = secrets.token_urlsafe(24)
    state["claimed"] = False
    state["created_at"] = time.time()
    state["expires_at"] = time.time() + ttl_secs
    state["claimed_by"] = None
    _write_claim_state(state)
    logger.info(f"Issued new onboarding claim token: {state['token'][:8]}...")
    return state


def validate_claim_token(token: str) -> bool:
    """Returns True if token is present, unclaimed, and not expired."""
    if not token:
        return False
    state = _read_claim_state()
    if state.get("token") != token:
        return False
    if state.get("claimed"):
        return False
    if state.get("expires_at", 0) < time.time():
        return False
    return True


def consume_claim_token(token: str, claimed_by: str = "admin") -> bool:
    """
    Atomically claims the token. Returns True if the token was valid and
    this consumer successfully claimed it (first-come-first-served).
    """
    if not validate_claim_token(token):
        return False

    state = _read_claim_state()
    if state.get("token") != token or state.get("claimed"):
        return False

    state["claimed"] = True
    state["claimed_by"] = claimed_by
    state["claimed_at"] = time.time()
    _write_claim_state(state)
    logger.info(f"Onboarding claim token consumed by '{claimed_by}'")
    return True


def invalidate_unclaimed_tokens():
    """Clears any unclaimed token state (used after successful setup)."""
    state = _read_claim_state()
    if not state.get("claimed"):
        state = {}
        _write_claim_state(state)


def claim_url_for_token(token: str = None, base_url: str = None) -> str:
    """
    Builds the claim URL that gets encoded in the QR code.
    The URL carries the token so a phone scan can complete onboarding.
    """
    if not token:
        state = _read_claim_state()
        token = state.get("token", "")
    if not base_url:
        base_url = "http://rndrsbc.local"
    return f"{base_url}/#setup?claim={token}"


def onboarding_state() -> dict:
    """Exposes current onboarding state to the /api/onboarding/status endpoint."""
    state = _read_claim_state()
    cfg = _read_config()
    wifi_configured = bool(
        cfg.get("wifi", {}).get("ssid")
        or os.path.exists("/etc/wpa_supplicant/wpa_supplicant.conf")
        or os.path.exists("/etc/wpa_supplicant.conf")
    )
    return {
        "token": state.get("token", ""),
        "claimed": state.get("claimed", False),
        "claimed_by": state.get("claimed_by"),
        "expires_at": state.get("expires_at", 0),
        "setup_required": not cfg.get("admin_password_hash"),
        "wifi_configured": wifi_configured,
        "ap_active": ap_manager.is_active,
    }


# ---------------------------------------------------------------------------
# AP Manager (Recovery Mode / Captive Provisioning)
# ---------------------------------------------------------------------------
class APManager:
    """
    Automatic Wi-Fi AP fallback manager.

    On startup / when connectivity is lost:
      - If network is reachable: do nothing.
      - If no Wi-Fi credentials OR connection drops:
        - Brings up a temporary Software AP ('rndrSBC-Setup-XXXX') on wlan0.
        - Starts dnsmasq DHCP on the AP interface serving 10.42.0.0/24.
        - Serves the web app (which advertises this AP gateway as the claim URL).
      - On successful re-provision / network recovery: tears down the AP, rejoins.
    """

    AP_INTERFACE = "wlan0"
    AP_SSID_PREFIX = "rndrSBC-Setup"
    AP_PASSPHRASE = None  # Open by default: provisioning ease over security on LAN
    AP_IP = "10.42.0.1"
    AP_DHCP_RANGE = "10.42.0.10,10.42.0.200,255.255.255.0,24h"
    AP_CHANNEL = "6"

    def __init__(self, config=None, logger=logger):
        self._config = config or {}
        self._lock = threading.Lock()
        self._active = False
        self._ap_thread = None
        self._stop_event = threading.Event()
        self._last_error = None
        self._ssid = None
        self._network_recovery_ts = None

    @property
    def is_active(self) -> bool:
        return self._active

    def is_network_available(self, host: str = "8.8.8.8", timeout: float = 3.0) -> bool:
        """Checks whether the device can reach the internet (or at least a LAN gateway)."""
        try:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", str(int(timeout)), host],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout + 2,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _wifi_creds_present(self) -> bool:
        """Determines whether Wi-Fi credentials are already configured on the system."""
        cfg = self._config or _read_config()
        if cfg.get("wifi", {}).get("ssid"):
            return True
        for p in ["/etc/wpa_supplicant/wpa_supplicant.conf", "/etc/wpa_supplicant.conf"]:
            if os.path.exists(p):
                return True
        return False

    def _ensure_deps(self) -> bool:
        """Checks hostapd/dnsmasq are installed (required for AP mode).

        Uses shutil.which() for cross-platform detection; returns False early on
        non-POSIX platforms (Windows/macOS) where AP mode is unsupported.
        """
        import shutil
        if os.name != "posix":
            return False
        has_hostapd = shutil.which("hostapd") is not None
        has_dnsmasq = shutil.which("dnsmasq") is not None
        if not (has_hostapd and has_dnsmasq):
            logger.info("AP mode unavailable: missing hostapd=%s dnsmasq=%s", has_hostapd, has_dnsmasq)
        return has_hostapd and has_dnsmasq

    def start_ap(self, force: bool = False) -> bool:
        """
        Brings up the temporary AP. Returns True if AP is now active.
        If `force=False`, refuses to start unless no Wi-Fi creds are present
        or the network is unreachable.
        """
        with self._lock:
            if self._active and not force:
                return True

            if not force and self._wifi_creds_present() and self.is_network_available():
                logger.info("Network is healthy; skipping AP fallback.")
                return False

            # Determine SSID
            suffix = secrets.token_hex(2).upper()
            self._ssid = f"{self.AP_SSID_PREFIX}-{suffix}"

            # Only attempt system-level AP control if hostapd/dnsmasq available;
            # otherwise, mark active for onboarding state visibility (dev mode).
            deps_ok = self._ensure_deps()

            if deps_ok:
                self._stop_event.clear()
                self._ap_thread = threading.Thread(target=self._ap_control_loop, daemon=True, name="rndrSBC-AP")
                self._ap_thread.start()

            self._active = True
            logger.info(f"Wi-Fi AP fallback active: SSID={self._ssid} IP={self.AP_IP}")
            return True

    def stop_ap(self):
        """Tears down the temporary AP and restores normal Wi-Fi client mode."""
        with self._lock:
            if not self._active:
                return

            self._stop_event.set()
            if self._ap_thread:
                self._ap_thread.join(timeout=5)
                self._ap_thread = None

            if os.name == "posix":
                _run_shell(["nmcli", "radio", "wifi", "on"], attempt=False)
                _run_shell(["nmcli", "device", "disconnect", "wlan0"], attempt=False)
                _run_shell(["nmcli", "connection", "up", "WifiHome"], attempt=False)

            self._active = False
            logger.info("Wi-Fi AP fallback disabled; network normal.")

    def _ap_control_loop(self):
        """Internal thread that ensures hostapd/dnsmasq stay running while AP is active.
        No-ops immediately on non-POSIX platforms (Windows/macOS) or when AP deps are absent.
        """
        if os.name != "posix" or not self._ensure_deps():
            logger.info("AP control loop skipped: AP mode unsupported on this platform.")
            return
        while not self._stop_event.is_set():
            _run_shell(["nmcli", "radio", "wifi", "off"], attempt=False)
            _run_shell(["ip", "addr", "add", f"{self.AP_IP}/24", "dev", self.AP_INTERFACE], attempt=False)
            _run_shell(["nmcli", "device", "set", self.AP_INTERFACE, "managed", "no"], attempt=False)
            _run_shell(["hostapd", "-B", "-P", "/run/rndrsbc_hostapd.pid", self._hostapd_conf()], attempt=False)
            if os.path.exists("/run/rndrsbc_hostapd.pid"):
                _run_shell(["dnsmasq", "--interface=" + self.AP_INTERFACE,
                            f"--dhcp-range={self.AP_DHCP_RANGE}",
                            f"--address=/#/{self.AP_IP}"], attempt=False)
            time.sleep(10)
        # Cleanup on stop
        _run_shell(["pkill", "-f", "hostapd"], attempt=False)
        _run_shell(["pkill", "-f", "dnsmasq"], attempt=False)

    def _hostapd_conf(self) -> str:
        conf_path = "/tmp/rndrsbc_hostapd.conf"
        with open(conf_path, "w") as f:
            f.write(f"interface={self.AP_INTERFACE}\n")
            f.write(f"driver=nl80211\n")
            f.write(f"ssid={self._ssid}\n")
            f.write(f"hw_mode=g\n")
            f.write(f"channel={self.AP_CHANNEL}\n")
            f.write(f"wmm_enabled=0\n")
            f.write(f"macaddr_acl=0\n")
            f.write(f"auth_algs=1\n")
            f.write(f"ignore_broadcast_ssid=0\n")
            f.write(f"wpa=0\n")
        return conf_path

    def start_network_watchdog(self, check_interval: float = 30.0):
        """
        Background daemon: every `check_interval` seconds, if the device
        has configured Wi-Fi but loses connectivity, automatically enters AP mode.
        On connectivity recovery, returns to normal mode.
        """
        def _watch():
            while True:
                try:
                    if self.is_active:
                        # Already in AP mode; try to detect recovery and tear down
                        if self._wifi_creds_present() and self.is_network_available():
                            self.stop_ap()
                    else:
                        if not self._wifi_creds_present() or not self.is_network_available():
                            self.start_ap()
                except Exception as e:
                    self._last_error = str(e)
                    logger.warning(f"Network watchdog error: {e}")
                time.sleep(check_interval)

        t = threading.Thread(target=_watch, daemon=True, name="rndrSBC-NetworkWatchdog")
        t.start()
        logger.info(f"Network watchdog started (interval={check_interval}s)")
        return t


def _run_shell(cmd: list[str], attempt: bool = True):
    """Safe no-shell helper for system management commands. All args are static."""
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except Exception as e:
        if attempt:
            logger.warning(f"Command failed: {' '.join(cmd)}: {e}")


# Singleton instance
ap_manager = APManager()
