from pathlib import Path

import pytest
from outpost import crypto


def test_encrypt_passes_target_filename_to_sops(monkeypatch, tmp_path):
    """sops resolves .sops.yaml creation rules against the INPUT path.

    We hand it a tempfile, so without --filename-override pointing at the real
    target it matches no rule and fails with "no matching creation rules found" -
    which broke every save once an encrypted inventory existed.
    """
    target = tmp_path / "state" / "inventory.enc.yaml"
    captured = {}

    class Proc:
        returncode = 0
        stdout = "encrypted-body"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return Proc()

    monkeypatch.setattr(crypto.shutil, "which", lambda name: "/usr/local/bin/sops")
    monkeypatch.setattr(crypto.subprocess, "run", fake_run)

    crypto.encrypt_to(target, "version: 1\n")

    cmd = captured["cmd"]
    assert "--filename-override" in cmd
    assert cmd[cmd.index("--filename-override") + 1] == str(target)
    # the rule must see a path ending in .enc.yaml, not the tempfile
    assert str(target).endswith(".enc.yaml")
    assert target.read_text() == "encrypted-body"


def test_encrypt_surfaces_sops_failure(monkeypatch, tmp_path):
    class Proc:
        returncode = 1
        stdout = ""
        stderr = "error loading config: no matching creation rules found"

    monkeypatch.setattr(crypto.shutil, "which", lambda name: "/usr/local/bin/sops")
    monkeypatch.setattr(crypto.subprocess, "run", lambda cmd, **kw: Proc())

    with pytest.raises(crypto.SopsError, match="no matching creation rules"):
        crypto.encrypt_to(tmp_path / "x.enc.yaml", "version: 1\n")


def test_encrypt_cleans_up_the_tempfile(monkeypatch, tmp_path):
    seen = {}

    class Proc:
        returncode = 0
        stdout = "body"
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen["tmp"] = cmd[-1]  # tempfile is the final argument
        return Proc()

    monkeypatch.setattr(crypto.shutil, "which", lambda name: "/usr/local/bin/sops")
    monkeypatch.setattr(crypto.subprocess, "run", fake_run)

    crypto.encrypt_to(tmp_path / "out.enc.yaml", "version: 1\n")
    # plaintext secrets must not linger in /tmp
    assert not Path(seen["tmp"]).exists()


def test_is_encrypted_path():
    assert crypto.is_encrypted_path(Path("state/inventory.enc.yaml"))
    assert not crypto.is_encrypted_path(Path("state/inventory.yaml"))
