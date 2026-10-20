"""Offline lock creation from externally published direct OCI manifest bytes."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from scripts.e2e.images import Images, ROLES
from scripts.e2e.safety import require
from scripts.planning_inputs import parse_snapshot, read_snapshot


def create(repositories, directory):
    repositories, directory = Path(repositories).absolute(), Path(directory).absolute()
    repos = parse_snapshot(read_snapshot(repositories))
    require(set(repos) == ROLES, "exact-fixture-role-repositories-required")
    records = {}
    for role in sorted(ROLES):
        raw = read_snapshot(directory / f"{role}.json")
        records[role] = {"reference": repos[role] + "@sha256:" + sha256(raw).hexdigest(), "manifest": f"{role}.json"}
    path = directory / "lock.json"
    # Write exclusively and validate before returning; no image/registry/Engine I/O.
    with path.open("x") as stream:
        json.dump({"version": 1, "images": records}, stream, sort_keys=True)
    return Images(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repositories", required=True)
    parser.add_argument("--directory", required=True)
    args = parser.parse_args()
    images = create(args.repositories, args.directory)
    print(json.dumps({"status": "passed", "images": len(images.records), "availability": "not-performed"}))


if __name__ == "__main__":
    main()
