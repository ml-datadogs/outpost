"""Thin wrapper around the `sops` binary for encrypting the inventory at rest.

If `sops` is not installed, the inventory is stored as plaintext YAML and a loud
warning is emitted. This keeps the tool usable for local development while making
encryption a one-step upgrade (install sops + age, add a .sops.yaml rule).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


class SopsError(RuntimeError):
    pass


def sops_available() -> bool:
    return shutil.which("sops") is not None


def is_encrypted_path(path: Path) -> bool:
    return path.name.endswith(".enc.yaml")


def decrypt(path: Path) -> str:
    """Return decrypted YAML text for an encrypted file."""
    if not sops_available():
        raise SopsError(
            f"{path} looks encrypted but `sops` is not installed. "
            "Install sops + age, or use the plaintext state/inventory.yaml."
        )
    proc = subprocess.run(
        ["sops", "--decrypt", str(path)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SopsError(f"sops decrypt failed: {proc.stderr.strip()}")
    return proc.stdout


def encrypt_to(path: Path, plaintext_yaml: str) -> None:
    """Encrypt `plaintext_yaml` and write the result to `path` (an .enc.yaml file).

    Recipients come from the repo's .sops.yaml creation rules (age).
    """
    if not sops_available():
        raise SopsError(
            "`sops` is not installed; cannot write encrypted inventory. "
            "Install sops + age or target the plaintext state/inventory.yaml."
        )
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as tf:
        tf.write(plaintext_yaml)
        tmp_path = tf.name
    try:
        # sops resolves .sops.yaml creation rules against the INPUT path. Without
        # --filename-override it sees the tempfile (/var/folders/.../tmpXXX.yaml),
        # matches no rule, and fails with "no matching creation rules found" - which
        # made every save fail as soon as an encrypted inventory existed.
        proc = subprocess.run(
            [
                "sops",
                "--encrypt",
                "--input-type",
                "yaml",
                "--output-type",
                "yaml",
                "--filename-override",
                str(path),
                tmp_path,
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise SopsError(f"sops encrypt failed: {proc.stderr.strip()}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(proc.stdout)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
