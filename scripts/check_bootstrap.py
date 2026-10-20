"""Offline repository checks; no deployment runtime or network operations."""

import argparse
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

try:
    import yaml
except ImportError:
    raise SystemExit("Install requirements-dev.txt; see README.md local setup")


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / ".agents/skills"
REQUIRED = {
    "task-development-workflow", "standards-sync", "code-quality", "docker",
    "github-actions", "continuous-delivery", "caddy-gateway", "gitops-deployment",
    "documentation", "licensing",
}
EXCLUDED = {".git", ".venv", "__pycache__", ".aws", ".codex"}
TEXT_SUFFIXES = {".md", ".yaml", ".yml", ".py", ".txt"}
TEXT_NAMES = {"Makefile", ".gitignore", ".editorconfig"}


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate mapping keys rather than silently overwriting values."""


def mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in result:
            raise ValueError(f"duplicate YAML key {key!r} at line {key_node.start_mark.line + 1}")
        result[key] = loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)


def load_yaml(content):
    return yaml.load(content, Loader=UniqueLoader)


def files():
    for directory, dirs, names in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED)
        for name in sorted(names):
            path = Path(directory) / name
            if path.suffix in TEXT_SUFFIXES or name in TEXT_NAMES:
                yield path


def normalize(content):
    return "\n".join(line.rstrip() for line in content.splitlines()).rstrip() + "\n"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_lock():
    for relative in ("AGENTS.md", "ARCHITECTURE.md", "README.md", "README.ru.md",
                     ".gitignore", ".editorconfig", "Makefile", "requirements-dev.txt",
                     "docs/roadmap.md", "scripts/check_bootstrap.py"):
        require((ROOT / relative).is_file(), f"missing bootstrap file: {relative}")
    lock = load_yaml((ROOT / ".codex-standards.lock.yaml").read_text())
    require(lock["version"] == 1, "unsupported lock version")
    source = lock["source"]
    require(source["repository"] == "https://github.com/DanilFaritovich/codex-development-standards",
            "unexpected standards upstream")
    require(source["branch"] == "develop", "unexpected standards source branch")
    require(re.fullmatch(r"[0-9a-f]{40}", source["ref"]), "lock requires full commit SHA")
    require(lock["profile"] == {"name": "docker-deployment-platform", "version": 1},
            "review profile change and update validation rules")
    selected = lock["skills"] + lock["optional_skills"]
    require(len(selected) == len(set(selected)), "duplicate skill selection")
    require(set(lock["skills"]) == REQUIRED, "required skills differ from profile v1")
    require(set(lock["optional_skills"]) <= {"ansible", "python-testing", "structured-logging"},
            "unknown optional skill")
    installed = {p.name for p in SKILLS.iterdir() if p.is_dir()}
    require(installed == set(selected), "installed directories differ from lock selection")
    inventory = lock["package_files"]
    require(set(inventory) == set(selected), "package inventory differs from selection")
    for name in selected:
        package = SKILLS / name
        actual = sorted(str(p.relative_to(package)) for p in package.rglob("*") if p.is_file())
        require(actual == sorted(inventory[name]), f"incomplete/changed package inventory: {name}")
        require("SKILL.md" in actual, f"missing SKILL.md: {name}")
        for relative in actual:
            path = package / relative
            require(not path.is_symlink() and path.stat().st_size > 0,
                    f"empty or symlinked package asset: {name}/{relative}")
        content = (package / "SKILL.md").read_text()
        frontmatter = re.match(r"\A---\n(.*?)\n---\n", content, re.S)
        require(frontmatter, f"missing skill frontmatter: {name}")
        metadata = load_yaml(frontmatter[1])
        require(metadata["name"] == name and bool(metadata["description"]),
                f"invalid skill metadata: {name}")
    require((SKILLS / "UPSTREAM-LICENSE.txt").is_file(), "missing upstream license notice")
    return len(selected)


def prose(content):
    # Links/examples inside fenced code are not documentation navigation.
    return re.sub(r"^```.*?^```[^\n]*$", "", content, flags=re.M | re.S)


def anchors(content):
    found = set()
    counts = {}
    for heading in re.findall(r"^#{1,6}\s+(.+)$", prose(content), re.M):
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        found.add(f"{slug}-{count}" if count else slug)
    return found


def check_links(path, content):
    for link in re.findall(r"\[[^\]\n]+\]\(([^)\n]+)\)", prose(content)):
        target = urlsplit(link.strip("<>"))
        if target.scheme or target.netloc:
            continue
        destination = (path.parent / unquote(target.path)).resolve() if target.path else path
        require(destination.is_relative_to(ROOT), f"link escapes repository: {path.name}: {link}")
        require(destination.exists(), f"broken link: {path.relative_to(ROOT)}: {link}")
        if target.fragment and destination.suffix == ".md":
            require(unquote(target.fragment) in anchors(destination.read_text()),
                    f"broken anchor: {path.name}: {link}")


def check_readmes():
    en = (ROOT / "README.md").read_text()
    ru = (ROOT / "README.ru.md").read_text()
    lock = load_yaml((ROOT / ".codex-standards.lock.yaml").read_text())
    require("(README.ru.md)" in en and "(README.md)" in ru, "missing language links")
    stages = lambda text: re.findall(r"^\| Stage (\d{2}) \| ([^|]+)\|", text, re.M)
    require(stages(en) == stages(ru) and len(stages(en)) == 9, "README roadmap mismatch")
    require(len(re.findall(r"^## ", en, re.M)) == len(re.findall(r"^## ", ru, re.M)),
            "README section-count mismatch")
    # Shared technical facts and setup must remain consistent across translations.
    for text in (en, ru):
        for token in REQUIRED | {lock["source"]["ref"],
                                 "docker-deployment-platform v1", "Planned"}:
            require(token in text, f"README missing shared fact: {token}")
    blocks = lambda text: re.findall(r"```sh\n(.*?)```", text, re.S)
    require(blocks(en) == blocks(ru) and bool(blocks(en)), "README setup-command mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fix", action="store_true", help="normalize project-owned text only")
    options = parser.parse_args()
    paths = list(files())
    if options.fix:
        changed = 0
        for path in paths:
            if path.is_relative_to(ROOT / ".agents"):
                continue
            content = path.read_bytes().decode("utf-8")
            fixed = normalize(content)
            if content != fixed:
                path.write_text(fixed, encoding="utf-8")
                changed += 1
        print(f"Normalized {changed} project-owned files; vendored skills preserved")
        return
    errors = []
    for path in paths:
        try:
            content = path.read_bytes().decode("utf-8")
            if not path.is_relative_to(ROOT / ".agents"):
                require(content == normalize(content), f"text normalization required: {path.relative_to(ROOT)}")
            if path.suffix in {".yaml", ".yml"}:
                load_yaml(content)
            if path.suffix == ".md":
                check_links(path, content)
        except (ValueError, OSError, yaml.YAMLError, TypeError) as error:
            errors.append(str(error))
    for check in (check_lock, check_readmes):
        try:
            check()
        except (ValueError, OSError, yaml.YAMLError, KeyError, TypeError, AttributeError) as error:
            errors.append(f"{check.__name__}: {error}")
    if errors:
        raise SystemExit("\n".join(f"ERROR: {error}" for error in errors))
    print(f"OK: {len(paths)} text files; YAML, standards packages, local links, README structure/setup parity")


if __name__ == "__main__":
    main()
