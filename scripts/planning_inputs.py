"""Bounded local input adapters. Snapshot claims are fixture evidence only."""

import json
import os
from pathlib import Path
import stat

from scripts.reconciliation import (DesiredApplication, DesiredState, ObservedState,
                                    PlanningInputError, canonical, encode, normalized_spec, revision)
from scripts.validate_registry import schema_validator, validate_registry

MAX_BYTES = 2 * 1024 * 1024
MAX_DEPTH = 32
MAX_NODES = 100000


def build_desired_state(root, git_revision):
    """Use the complete Stage 02 admission API, then detach and normalize."""
    revision(git_revision)
    registry = validate_registry(Path(root))
    return DesiredState(git_revision, tuple(sorted((
        DesiredApplication(app["metadata"]["id"], app["metadata"]["sourceRepository"],
                           encode(normalized_spec(app["spec"])))
        for app in registry.applications), key=lambda app: app.id)))


def fail(rule):
    raise PlanningInputError(rule)


def bounded_tree(value, depth=0, budget=None):
    if budget is None:
        budget = [MAX_NODES]
    budget[0] -= 1
    if budget[0] < 0 or depth > MAX_DEPTH:
        fail("snapshot nesting/node limit exceeded")
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str or len(key) > 128:
                fail("snapshot object key invalid")
            bounded_tree(item, depth + 1, budget)
    elif type(value) is list:
        for item in value:
            bounded_tree(item, depth + 1, budget)
    elif type(value) is str:
        if len(value) > 512:
            fail("snapshot string limit exceeded")
    elif value is not None and type(value) not in (int, bool):
        fail("snapshot contains unsupported value type")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail("duplicate JSON object key")
        result[key] = value
    return result


def parse_snapshot(data):
    if len(data) > MAX_BYTES:
        fail("snapshot byte limit exceeded")
    # Bound structural nesting before JSON object construction, ignoring strings.
    depth, quoted, escaped = 0, False, False
    for char in data.decode("utf-8"):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > MAX_DEPTH:
                fail("snapshot nesting limit exceeded")
        elif char in "]}":
            depth -= 1
    return json.loads(data, object_pairs_hook=unique_object,
                      parse_constant=lambda _: fail("non-finite JSON number forbidden"))


def read_snapshot(path):
    """Walk with dirfds and NOFOLLOW; reject symlinks in every path segment."""
    path = Path(path)
    if ".." in path.parts or path.suffix != ".json":
        fail("snapshot requires a safe .json path without parent traversal")
    descriptor = None
    try:
        descriptor = os.open(path.anchor or ".", os.O_RDONLY | os.O_DIRECTORY)
        parts = path.parts[1:] if path.is_absolute() else path.parts
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        file_descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(file_descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                fail("snapshot must be a regular non-symlink file")
            return stream.read(MAX_BYTES + 1)
    except (OSError, IndexError):
        fail("snapshot unavailable or path contains symlink/non-regular entry")
    finally:
        if descriptor is not None:
            os.close(descriptor)


def load_observed_state(source):
    """Accept a local JSON path or a native JSON document; validate before freezing."""
    try:
        document = source if type(source) is dict else parse_snapshot(read_snapshot(source))
        bounded_tree(document)
        if len(encode(document)) > MAX_BYTES:
            fail("snapshot byte limit exceeded")
        if type(document) is not dict or document.get("apiVersion") != "portfolio.observed/v1":
            fail("unsupported observed apiVersion")
        if not schema_validator("observed").is_valid(document):
            fail("snapshot schema violation (unknown fields, types or bounds)")
        errors = set()
        ids = set()
        for app in document["applications"]:
            if app["id"] in ids:
                errors.add("duplicate application identity")
            ids.add(app["id"])
            names = set()
            for component in app["components"]:
                if component["name"] in names:
                    errors.add("duplicate component identity")
                names.add(component["name"])
                if component["existence"] != "present" and (component["runtime"] is not None or component["imageIdentity"] is not None):
                    errors.add("non-present component has runtime evidence")
                if component["runtime"]:
                    mounts = component["runtime"].get("volumeMounts", [])
                    for key in ("volume", "mountPath"):
                        values = [m[key] for m in mounts]
                        if len(values) != len(set(values)):
                            errors.add("duplicate observed mount identity")
            if app["existence"] != "present" and (app["components"] or app["route"]["existence"] == "present"):
                errors.add("non-present application has present resources")
            route = app["route"]
            if (route["existence"] == "present") != (route["configuration"] is not None):
                errors.add("route existence/configuration inconsistent")
            if app["storage"] and app["storage"]["type"] == "persistent":
                names = [v["name"] for v in app["storage"]["volumes"]]
                if len(names) != len(set(names)):
                    errors.add("duplicate observed volume identity")
        if errors:
            fail("; ".join(sorted(errors)))
        # Normalize optional runtime lists without inferring unknown runtime.
        document = json.loads(encode(document))
        for app in document["applications"]:
            for component in app["components"]:
                if component["runtime"]:
                    for key in ("dependsOn", "configRefs", "secretRefs", "volumeMounts"):
                        component["runtime"].setdefault(key, [])
        return ObservedState(encode(canonical(document)))
    except (UnicodeError, json.JSONDecodeError, RecursionError, TypeError, OverflowError, ValueError) as error:
        if isinstance(error, PlanningInputError):
            raise
        fail("invalid snapshot JSON or unsupported input")
