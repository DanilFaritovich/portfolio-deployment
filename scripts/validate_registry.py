"""Bounded, read-only registry validation. No Docker, network or runtime access."""

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat

try:
    import yaml
    from jsonschema import Draft202012Validator, validators
    from jsonschema.exceptions import SchemaError
except ImportError:
    raise SystemExit("Install requirements-dev.txt; see README.md local setup") from None

ROOT = Path(__file__).resolve().parents[1]
VERSION = "portfolio.deployment/v1"
MAX_FILE_BYTES = 65536
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_FILES = 128
MAX_DEPTH = 32
MAX_NODES = 4096
# YAML floats such as 8080.0 are not integer declarations, even when integral.
StrictValidator = validators.extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda checker, value: type(value) is int
    ),
)


class RegistryValidationError(ValueError):
    """A deterministic collection of errors; never a partially trusted registry."""

    def __init__(self, errors):
        self.errors = tuple(sorted(set(errors)))
        super().__init__("\n".join(self.errors))


@dataclass(frozen=True)
class ValidatedRegistry:
    """Owned application documents in filename order, validated against policy."""

    applications: tuple[dict, ...]


def reject(message):
    raise RegistryValidationError([message])


def read_bounded(path):
    """Reject links/non-files and cap reads, including a concurrently growing file."""
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            reject("expected a regular file (symlinks forbidden)")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                reject("expected a regular file (symlinks forbidden)")
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            reject("file exceeds byte limit")
        return data
    except OSError:
        reject("cannot read required file")


class StrictLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        if not isinstance(key_node, yaml.ScalarNode) or key_node.tag != "tag:yaml.org,2002:str":
            reject("mapping keys must be strings")
        key = loader.construct_object(key_node)
        if key == "<<":
            reject("YAML merge keys forbidden")
        if key in result:
            reject(f"duplicate YAML key at line {key_node.start_mark.line + 1}")
        result[key] = loader.construct_object(value_node)
    return result


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def load_document(data):
    """Load bounded UTF-8 JSON-compatible YAML, with no aliases/tags/directives."""
    if len(data) > MAX_FILE_BYTES:
        reject("file exceeds byte limit")
    try:
        content = data.decode("utf-8")
        for token in yaml.scan(content):
            if isinstance(token, (yaml.AliasToken, yaml.AnchorToken, yaml.TagToken,
                                  yaml.DirectiveToken)):
                reject("YAML aliases, anchors, explicit tags and directives forbidden")
        depth = nodes = documents = 0
        for event in yaml.parse(content):
            if isinstance(event, yaml.DocumentStartEvent):
                documents += 1
                if documents > 1:
                    reject("multiple YAML documents forbidden")
            if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent, yaml.ScalarEvent)):
                nodes += 1
                if nodes > MAX_NODES:
                    reject("YAML node limit exceeded")
            if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
                depth += 1
                if depth > MAX_DEPTH:
                    reject("YAML nesting limit exceeded")
            elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
                depth -= 1
        document = yaml.load(content, Loader=StrictLoader)
    except (UnicodeError, yaml.YAMLError, ValueError, RecursionError) as error:
        if isinstance(error, RegistryValidationError):
            raise
        # Parser diagnostics can include untrusted values; report only location.
        mark = getattr(error, "problem_mark", None)
        reject("invalid UTF-8/YAML" + (f" at line {mark.line + 1}" if mark else ""))
    if not isinstance(document, dict):
        reject("document must be a mapping")
    return document


def schema_validator(name):
    try:
        schema = json.loads((ROOT / "schemas" / f"{name}-v1.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        return StrictValidator(schema)
    except (OSError, ValueError, SchemaError) as error:
        reject(f"required {name} schema unavailable or invalid: {type(error).__name__}")


def structure(document, validator):
    if document.get("apiVersion") != VERSION:
        reject("unsupported apiVersion")
    errors = []
    for error in validator.iter_errors(document):
        location = "/".join(str(part) for part in error.absolute_path) or "$"
        errors.append(f"{location}: schema rule {error.validator} failed")
    if errors:
        raise RegistryValidationError(errors)


def duplicates(values):
    return len(values) != len(set(values))


def semantic(document):
    spec = document["spec"]
    components = spec["components"]
    names = [component["name"] for component in components]
    errors = []
    if duplicates(names):
        errors.append("duplicate component name")
    by_name = {component["name"]: component for component in components}
    volumes = [volume["name"] for volume in spec["storage"].get("volumes", [])]
    if duplicates(volumes):
        errors.append("duplicate volume name")
    used_volumes = set()
    for component in components:
        name = component["name"]
        dependencies = component.get("dependsOn", [])
        if any(dep not in by_name for dep in dependencies):
            errors.append(f"{name}: unknown dependency")
        for field in ("configRefs", "secretRefs"):
            if not set(component.get(field, [])) <= set(spec.get(field, [])):
                errors.append(f"{name}: undeclared {field}")
        mounts = component.get("volumeMounts", [])
        if duplicates([mount["volume"] for mount in mounts]) or duplicates([mount["mountPath"] for mount in mounts]):
            errors.append(f"{name}: duplicate volume mount")
        for mount in mounts:
            used_volumes.add(mount["volume"])
            if mount["volume"] not in volumes:
                errors.append(f"{name}: undeclared volume")
            target = mount["mountPath"]
            if any(target == prefix or target.startswith(prefix + "/") for prefix in
                   ("/dev", "/proc", "/sys", "/run", "/var/run")):
                errors.append(f"{name}: reserved mount path")
    if set(volumes) != used_volumes:
        errors.append("persistent volumes must be declared and used")
    visiting, visited = set(), set()

    def visit(name):
        if name in visiting:
            return True
        if name in visited:
            return False
        visiting.add(name)
        for dependency in by_name[name].get("dependsOn", []):
            if dependency in by_name and visit(dependency):
                return True
        visiting.remove(name)
        visited.add(name)
        return False

    if any(visit(name) for name in names):
        errors.append("cyclic component dependencies")
    ingress = spec.get("ingress")
    if ingress:
        target = by_name.get(ingress["component"])
        if not target or target["type"] != "http" or target["port"] != ingress["port"]:
            errors.append("ingress must target an HTTP component with matching port")
    return errors


def admission(document, policies):
    app_id = document["metadata"]["id"]
    policy = policies.get(app_id)
    if policy is None:
        return ["application not admitted"]
    errors = []
    if document["metadata"]["sourceRepository"] != policy["sourceRepository"]:
        errors.append("source repository not admitted")
    for component in document["spec"]["components"]:
        if component["image"].split("@", 1)[0] not in policy["imageRepositories"].get(component["name"], []):
            errors.append(f"{component['name']}: image repository not admitted")
        if component["platform"] not in policy["platforms"]:
            errors.append(f"{component['name']}: platform not admitted")
    ingress = document["spec"].get("ingress")
    if ingress and ingress["hostname"] not in policy["hostnames"]:
        errors.append("hostname not admitted")
    return errors


def validate_registry(root=ROOT):
    """Validate the entire registry or raise RegistryValidationError; never mutate.

    root contains registry/applications and policy/admission.yaml. Schemas always
    come from this validator's installation, never from the untrusted registry.
    Disabled applications undergo every validation and reserve their identities.
    """
    root = Path(root)
    directory = root / "registry" / "applications"
    errors = []
    documents = []
    try:
        for path in (root / "registry", directory, root / "policy"):
            if not stat.S_ISDIR(path.lstat().st_mode):
                reject("required registry/policy directory missing or symlinked")
        paths = []
        for path in directory.iterdir():
            if path.name == ".gitkeep" and path.is_file() and not path.is_symlink() and path.stat().st_size == 0:
                continue
            paths.append(path)
            if len(paths) > MAX_FILES:
                reject("registry file count limit exceeded")
        paths.sort()
        total = 0
        inputs = []
        for path in [root / "policy" / "admission.yaml", *paths]:
            data = read_bounded(path)
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                reject("registry total byte limit exceeded")
            if path in paths and (path.suffix != ".yaml" or not re.fullmatch(r"[a-z][a-z0-9-]*\.yaml", path.name)):
                reject("registry contains an unexpected filename")
            inputs.append((path, data))
    except OSError:
        reject("required registry/policy directory unavailable")
    app_validator = schema_validator("application")
    policy_validator = schema_validator("admission")
    policy = None
    for index, (path, data) in enumerate(inputs):
        label = str(path.relative_to(root))
        try:
            document = load_document(data)
            structure(document, policy_validator if index == 0 else app_validator)
            if index == 0:
                policy = document
            else:
                documents.append((label, document))
        except RegistryValidationError as error:
            errors.extend(f"{label}: {message}" for message in error.errors)
    policies = {}
    if policy is not None:
        for entry in policy["applications"]:
            if entry["id"] in policies:
                errors.append("policy/admission.yaml: duplicate application ID")
            policies[entry["id"]] = entry
        hosts = [host for entry in policy["applications"] for host in entry["hostnames"]]
        if duplicates(hosts):
            errors.append("policy/admission.yaml: duplicate hostname")
    ids, hosts, aliases = set(), set(), set()
    for label, document in documents:
        app_id = document["metadata"]["id"]
        if app_id in ids:
            errors.append(f"{label}: duplicate application ID")
        ids.add(app_id)
        if Path(label).stem != app_id:
            errors.append(f"{label}: filename must match application ID")
        for component in document["spec"]["components"]:
            alias = f"{app_id}-{component['name']}"
            if len(alias) > 63:
                errors.append(f"{label}: global component alias exceeds 63 characters")
            if alias in aliases:
                errors.append(f"{label}: global component alias collision")
            aliases.add(alias)
        ingress = document["spec"].get("ingress")
        if ingress:
            if ingress["hostname"] in hosts:
                errors.append(f"{label}: duplicate hostname")
            hosts.add(ingress["hostname"])
        messages = semantic(document)
        if policy is not None:
            messages += admission(document, policies)
        errors.extend(f"{label}: {message}" for message in messages)
    if errors:
        raise RegistryValidationError(errors)
    return ValidatedRegistry(tuple(document for _, document in documents))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="registry/policy root")
    options = parser.parse_args()
    try:
        result = validate_registry(options.root)
    except RegistryValidationError as error:
        for message in error.errors:
            print(f"ERROR: {message}")
        return 1
    print(f"OK: registry validated ({len(result.applications)} applications); offline only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
