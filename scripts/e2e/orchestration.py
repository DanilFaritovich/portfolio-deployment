"""Single-use host orchestration of the existing Multipass lifecycle."""
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import sys
import re

from scripts.docker_adapter import DockerError
from scripts.e2e.host_state import Store
from scripts.e2e.images import Images
from scripts.e2e.safety import require, session_id
from scripts.e2e.vm import VM
from scripts.planning_inputs import parse_snapshot, read_snapshot


def confirm(message, token):
    print(message, flush=True)
    require(sys.stdin.isatty(), "e2e-interactive-confirmation-required")
    try:
        answer = input(f"Type {token} to continue [default: deny]: ")
    except EOFError:
        answer = ""
    require(answer == token, "e2e-operation-declined")


def image_fingerprint(path):
    images = Images(path)
    return sha256(json.dumps(images.records, sort_keys=True).encode()).hexdigest()


def current(store):
    pointer = store.read("current.json")
    if pointer is None:
        require(not any(re.fullmatch(r"[0-9a-f]{16}\.json", name) for name in os.listdir(store.fd)),
                "e2e-host-pointer-missing-diagnose-required")
        return None
    require(set(pointer) == {"version", "session"} and pointer["version"] == 1, "invalid-e2e-host-pointer")
    session = session_id(pointer["session"])
    state = store.read(session + ".json")
    fields = {"version", "session", "vm", "phase", "status", "mutationStarted", "images", "identity", "result"}
    require(isinstance(state, dict) and set(state) == fields and state["version"] == 1 and
            state["session"] == session and state["vm"] == f"pd-e2e-{session}" and
            state["phase"] in ("planned", "prepare", "images", "run", "complete") and
            state["status"] in ("planned", "in-progress", "passed", "blocked", "failed", "unknown") and
            type(state["mutationStarted"]) is bool and isinstance(state["images"], str) and
            re.fullmatch(r"[0-9a-f]{64}", state["images"]) and isinstance(state["result"], dict) and
            (state["identity"] is None or isinstance(state["identity"], dict)) and
            (state["mutationStarted"] == (state["phase"] != "planned")) and
            ((state["status"] == "passed") == (state["phase"] == "complete")), "invalid-e2e-host-receipt")
    return state


def identity(machine):
    data = json.loads(machine.guest("/opt/portfolio-e2e/.venv/bin/python", "-c",
        "import sys,json; sys.path.insert(0, '/opt/portfolio-e2e'); "
        "from scripts.e2e.safety import private_json; "
        f"print(json.dumps(private_json('/var/lib/portfolio-e2e/{machine.session}/identity.json')))"))
    require(set(data) == {"session", "machine", "daemon"} and data["session"] == machine.session and
            all(isinstance(v, str) and v for v in data.values()), "invalid-e2e-vm-identity")
    return data


def run(lock, directory, *, factory=VM, consent=confirm):
    # Validate before any hypervisor command or confirmation.
    fingerprint = image_fingerprint(lock)
    with Store(directory).locked() as store:
        previous = current(store)
        require(previous is None or previous["status"] == "passed" and previous["phase"] == "complete" or
                not previous["mutationStarted"], "previous-e2e-unfinished-diagnose-required")
        session = secrets.token_hex(8)
        machine = factory(session)
        prerequisites = machine.prerequisites()
        if prerequisites["status"] != "passed":
            return {**prerequisites, "reason": "e2e-host-prerequisites-blocked",
                    "missing": [name for name, passed in prerequisites.get("checks", {}).items() if not passed]}
        # Freeze the validated lock and exact raw manifests in a private session directory.
        snapshot = store.path / session
        snapshot.mkdir(mode=0o700)  # Exclusive; never adopt a collision.
        lock_raw = read_snapshot(Path(lock))
        doc = parse_snapshot(lock_raw)
        # Revalidate filenames from these exact bytes: the producer file may
        # have changed since initial validation. No untrusted write paths.
        require(isinstance(doc, dict) and isinstance(doc.get("images"), dict) and
                all(isinstance(r, dict) and isinstance(r.get("manifest"), str) and
                    re.fullmatch(r"[a-z0-9-]+\.json", r["manifest"]) and r["manifest"] != "lock.json"
                    for r in doc["images"].values()), "invalid-e2e-snapshot-path")
        for name, raw in [("lock.json", lock_raw), *[
                (r["manifest"], read_snapshot(Path(lock).absolute().parent / r["manifest"]))
                for r in doc["images"].values()]]:
            target = snapshot / name
            if target.exists():
                require(target.read_bytes() == raw, "conflicting-e2e-image-snapshot")
            else:
                with target.open("xb") as stream:
                    stream.write(raw)
                target.chmod(0o600)
        pinned = snapshot / "lock.json"
        require(image_fingerprint(pinned) == fingerprint, "e2e-lock-changed")
        state = {"version": 1, "session": session, "vm": machine.name, "phase": "planned", "status": "planned",
                 "mutationStarted": False, "images": fingerprint, "identity": None, "result": {}}
        store.write(session + ".json", state)
        store.write("current.json", {"version": 1, "session": session})
        print(json.dumps({"session": session, "vm": machine.name, "receipt": str(store.path / (session + ".json")),
                          "diagnose": f"make e2e-diagnose E2E_SESSION={session} E2E_AUTHORIZE=diagnose:{session}"}), flush=True)
        try:
            for phase, message in (
                ("prepare", "Create a NEW Ubuntu 24.04 VM (2 CPUs / 4 GiB / 16 GiB); download OS/packages/dependencies."),
                ("images", "Pull six public immutable GHCR fixtures inside the owned VM; no credentials are transferred."),
                ("run", "Run real Docker mutations and scenarios in the VM; retain all failure state."),
            ):
                consent(message, f"{phase}:{session}")
                if phase == "run":
                    consent("Authorize readiness-gated loopback Caddy activation inside this VM.", f"activate:{session}")
                require(image_fingerprint(pinned) == fingerprint, "e2e-lock-changed")
                if state["identity"] is not None:
                    require(identity(machine) == state["identity"], "e2e-vm-identity-changed")
                state.update(phase=phase, status="in-progress", mutationStarted=True)
                store.write(session + ".json", state)  # Durable intent before mutation.
                if phase == "prepare":
                    result = machine.prepare(f"prepare:{session}")
                elif phase == "images":
                    machine.transfer_images(pinned)
                    result = machine.invoke("images", f"images:{session}")
                else:
                    result = machine.invoke("run", f"run:{session}", f"activate:{session}")
                require(isinstance(result, dict) and result.get("status") in ("passed", "failed", "blocked"),
                        "invalid-e2e-phase-result")
                state["result"] = result
                if result["status"] != "passed":
                    state["status"] = result["status"]
                    store.write(session + ".json", state)
                    return state
                if phase == "prepare":
                    state["identity"] = identity(machine)
                # Keep a nonterminal status until every separately authorized phase completes.
                state["status"] = "planned"
                store.write(session + ".json", state)
            state.update(phase="complete", status="passed")
            store.write(session + ".json", state)
            return state
        except BaseException as error:
            # SIGKILL leaves durable in-progress intent. Neither path retries/cleans.
            state["status"] = "unknown" if state["status"] == "in-progress" else "blocked"
            state["result"] = {"reason": error.code if isinstance(error, DockerError) else "e2e-interrupted-or-unconfirmed",
                               "realE2E": "unknown" if state["phase"] == "run" else "not-performed"}
            store.write(session + ".json", state)
            if isinstance(error, (KeyboardInterrupt, Exception)):
                return state
            raise
