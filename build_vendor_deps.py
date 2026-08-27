"""
rndrSBC - Build the vendored dependency bundle (fully offline runtime).

Run ON THE TARGET (or with --platform for cross-arch wheels):

    python3 build_vendor_deps.py                # for this machine's arch
    python3 build_vendor_deps.py --pi2pi-armv7l # Raspberry Pi 32-bit (armv7l)
    python3 build_vendor_deps.py aarch64        # Raspberry Pi 64-bit / modern

This downloads the exact runtime deps (pillow, requests, werkzeug) as
wheels into ``vendor/deps/``. core/paths.bootstrap_deps() then adds them to
sys.path, so the platform runs with ZERO pip installs and NO internet.

The wheels are architecture-specific portable ``manylinux`` builds, so a
bundle built for the Pi can ship as-is in the deployed folder.
"""

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(ROOT, "vendor", "deps")


def parse_platform(spec: str):
    """Map friendly names to platform tags pip download understands."""
    table = {
        "aarch64": "manylinux2014_aarch64",
        "armv7l":  "manylinux2014_armv7l",   # = Pi 32-bit
        "armhf":   "manylinux2014_armv7l",
        "x86_64":  "manylinux2014_x86_64",
        "amd64":   "manylinux2014_x86_64",
    }
    return table.get(spec, spec)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--platform",
        help="Target platform tag (e.g. aarch64, armv7l, x86_64). "
             "Omit to detect from the current machine.",
    )
    ap.add_argument(
        "--pip", default=sys.executable,
        help="Python interpreter to invoke pip with (default: current venv).",
    )
    args = ap.parse_args()

    os.makedirs(VENDOR, exist_ok=True)
    req = os.path.join(ROOT, "requirements.txt")
    cmd = [args.pip, "-m", "pip", "download",
           "-r", req,
           "--only-binary=:all:",
           "-d", VENDOR]
    if args.platform:
        tag = parse_platform(args.platform)
        # Cross-arch download. Pillow ships manylinux wheels that work for
        # any recent Python, and pure-python wheels (requests/werkzeug/certifi)
        # are ABI-agnostic, so a generic --python-version is safe here.
        # Two tags: the requested platform plus "any" for the pure-python deps.
        cmd += ["--platform", tag,
                "--platform", "any",
                "--python-version", "3.10"]

    print(f"Downloading runtime wheels -> {VENDOR}")
    subprocess.check_call(cmd)
    wheels = [f for f in os.listdir(VENDOR) if f.endswith(".whl")]
    print(f"Done. {len(wheels)} wheels vendored.")
    for w in sorted(wheels):
        print("  ", w)


if __name__ == "__main__":
    main()
