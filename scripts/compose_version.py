"""Shared Compose CLI support policy; version acceptance is not runtime evidence."""
import re


def supported_compose(value):
    # Preserve the existing v2 line; admit only the reviewed v5 release, rather
    # than assuming that future majors/minors retain the same CLI semantics.
    if not isinstance(value, str) or len(value) > 80:
        return False
    match = re.fullmatch(r'v?(2|5)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\+[A-Za-z0-9.~+-]+)?', value)
    return bool(match and (match[1] == '2' or tuple(map(int, match.group(1, 2, 3))) == (5, 6, 0)))
