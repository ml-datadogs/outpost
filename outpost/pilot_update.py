"""Roll out one staged core binary. A transient VPS timer rolls back on operator disconnect."""

from __future__ import annotations

import fcntl
import shlex
import uuid
from pathlib import Path
from typing import Callable

from .config import settings
from .models import Node
from .server.bootstrap import _connect, _run

SERVICES = {
    "xray": "run -test -config /usr/local/etc/xray/config.json",
    "sing-box": "check -c /etc/sing-box/config.json",
}


def update(node: Node, service: str, binary: Path, verify: Callable[[], None]) -> None:
    if service not in SERVICES or not binary.is_file():
        raise ValueError("Choose xray or sing-box and a local Linux binary for the VPS architecture")
    state = settings.state_dir / "pilot-private"
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / "maintenance.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        verify()
        with _connect(node, settings, None) as client:
            # Derive the installed path from the running unit; replace on the same filesystem.
            pid = int(_run(client, f"systemctl show -p MainPID --value {service}").strip())
            if pid <= 1:
                raise RuntimeError(f"{service} must be running before an update")
            path = _run(client, f"readlink -f /proc/{pid}/exe").strip()
            if Path(path).name != service:
                raise RuntimeError("Unexpected service executable")
            name = "outpost-update-" + uuid.uuid4().hex
            target = shlex.quote(path)
            candidate = shlex.quote(f"{path}.{name}.new")
            backup = shlex.quote(f"{path}.{name}.bak")
            with client.open_sftp() as sftp:
                sftp.put(str(binary), f"{path}.{name}.new")
                sftp.chmod(f"{path}.{name}.new", 0o755)
            rollback = f"if test -f {backup}; then mv -f {backup} {target} && systemctl restart {service}; fi"
            try:
                _run(client, f"{candidate} {SERVICES[service]} && ln {target} {backup}")
                _run(client, f"systemd-run --unit={name} --on-active=10m /bin/sh -ec {shlex.quote(rollback)}")
                _run(client, f"mv -f {candidate} {target} && systemctl restart {service}")
                verify()
                _run(client, f"systemctl stop {name}.timer && test -f {backup} && rm -f {backup}")
            except BaseException:
                _run(client, rollback)
                raise
            finally:
                _run(client, f"rm -f {candidate}")
