"""Tests for password management CLI and API."""
import json
import os
import tempfile
from rndrsbc.__main__ import _auth_command
from werkzeug.security import check_password_hash

def test_cli_set_and_reset_password(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg_path = os.path.join(tmpdir, "config.json")
        with open(cfg_path, "w") as f:
            json.dump({"device": {"name": "Test"}}, f)
        
        monkeypatch.setattr("core.paths.CONFIG_PATH", cfg_path)
        
        # 1. Set password via CLI
        rc = _auth_command(["set-password", "supersecret123"])
        assert rc == 0
        
        with open(cfg_path, "r") as f:
            cfg = json.load(f)
        assert "admin_password_hash" in cfg
        assert check_password_hash(cfg["admin_password_hash"], "supersecret123")
        
        # 2. Reset password via CLI
        rc = _auth_command(["reset-password"])
        assert rc == 0
        with open(cfg_path, "r") as f:
            cfg = json.load(f)
        assert "admin_password_hash" not in cfg

def test_cli_set_password_rejects_short_password(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg_path = os.path.join(tmpdir, "config.json")
        with open(cfg_path, "w") as f:
            json.dump({}, f)
        monkeypatch.setattr("core.paths.CONFIG_PATH", cfg_path)
        
        rc = _auth_command(["set-password", "short"])
        assert rc == 1
