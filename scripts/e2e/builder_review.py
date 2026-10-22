"""Read-only guest inspection for failed builder preparation; no recovery mutations."""
import json
import os
from pathlib import Path
import re
import stat


def review(session, root=Path("/"), processes=Path("/proc"), owner=0):
    def need(condition):
        if not condition:
            raise ValueError("failed-builder-review-inconclusive")

    def read(name):
        path = root / name
        need(path.resolve() == path)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            need(stat.S_ISREG(info.st_mode) and info.st_uid == owner and info.st_nlink == 1 and info.st_size <= 65536)
            raw = stream.read(65537)
            need(len(raw) <= 65536)
        return raw

    marker_raw = read("etc/pd-fixture.json")
    need(not (root / "etc/pd-fixture.json").stat().st_mode & 0o077)
    marker = json.loads(marker_raw)
    machine = read("etc/machine-id").decode().strip()
    hostname = read("etc/hostname").decode().strip()
    need(re.fullmatch(r"[0-9a-f]{32}", machine) and hostname == "pd-fixture-builder-" + session)
    need(marker == {"session": session, "kind": "builder", "machine": machine})
    # Preparation never transfers payload/plan before cloud-init succeeds. Even a
    # transferred plan is continuation evidence, and therefore blocks this scope.
    folder = root / "var/lib/pd-fixtures"
    need(folder.resolve() == folder and folder.is_dir() and set(os.listdir(folder)) == {"context"})
    context = folder / "context"
    need(context.resolve() == context and context.is_dir() and not os.listdir(context))
    for name in ("var/lib/containers/storage", "root/.local/share/containers/storage"):
        path = root / name
        need(path.resolve() == path and not path.is_symlink() and (not path.exists() or path.is_dir() and not os.listdir(path)))
    for name in ("run/pd-fixtures-auth.json", "home/ubuntu/pd-fixture-images.tar",
                 "tmp/pd-fixture-plan.json", "tmp/pd-fixture-fixture_guest.py", "tmp/pd-fixture-images.tar",
                 "tmp/pd-fixture-Dockerfile", "tmp/pd-fixture-app.py"):
        need(not os.path.lexists(root / name))
    entries = list(processes.iterdir())
    need(len(entries) <= 32768)
    forbidden = {"buildah", "skopeo", "fixture_guest.py", "cloud-init"}
    for entry in entries:
        if not entry.name.isdecimal() or int(entry.name) == os.getpid():
            continue
        try:
            with (entry / "cmdline").open("rb") as stream:
                raw = stream.read(65537)
        except FileNotFoundError:
            continue  # Exited process; all unreadable live processes fail closed.
        need(len(raw) <= 65536)
        args = raw.decode("utf-8").split("\0")
        need(not any(Path(arg).name in forbidden for arg in args[:3]))
    return {"name": hostname, "hostname": hostname, "session": session, "kind": "builder",
            "machineId": machine, "marker": marker, "noGuestContinuation": True}
