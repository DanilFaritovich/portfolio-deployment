"""Small adapter for the infrastructure repository's static Ansible inventory."""

from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
import re
import stat

from scripts.validate_registry import load_document, read_bounded, RegistryValidationError
from scripts.production import InspectionError


@dataclass(frozen=True)
class Target:
    alias: str
    host: str
    port: int
    user: str
    key: Path

    @property
    def trust_name(self):
        return self.host if self.port == 22 else f"[{self.host}]:{self.port}"

    @property
    def document(self):
        return dict(alias=self.alias, host=self.host, port=self.port, user=self.user)


def local_path(value):
    # OpenSSH expands percent tokens even in argv options. Keep paths literal.
    if not isinstance(value, str) or not re.fullmatch(r"(?:/|~/)[A-Za-z0-9_./-]+", value):
        raise InspectionError("invalid-local-path")
    path = Path(value).expanduser()
    if '..' in path.parts or any(p.is_symlink() for p in [path, *path.parents]):
        raise InspectionError("unsafe-local-path")
    return path


def secure_file(path, *, private=False):
    try:
        if any(p.is_symlink() for p in [path, *path.parents]):
            raise InspectionError("unsafe-file-symlink")
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise InspectionError("unsafe-file-owner-or-type")
        if info.st_mode & (0o077 if private else 0o022):
            raise InspectionError("unsafe-file-permissions")
        parent = path.parent.stat()
        if parent.st_uid != os.getuid() or parent.st_mode & 0o022:
            raise InspectionError("unsafe-parent-permissions")
        if not os.access(path, os.R_OK):
            raise InspectionError("file-not-readable")
    except OSError:
        raise InspectionError("required-local-file-missing") from None


def load_inventory(path):
    try:
        data = load_document(read_bounded(Path(path)))
        if set(data) != {'all'} or set(data['all']) != {'children'}:
            raise ValueError
        children = data['all']['children']
        if set(children) != {'bootstrap'} or set(children['bootstrap']) != {'hosts'}:
            raise ValueError
        hosts = children['bootstrap']['hosts']
        if not isinstance(hosts, dict) or len(hosts) != 1:
            raise ValueError
        alias, values = next(iter(hosts.items()))
        allowed = {'ansible_host', 'ansible_port', 'ansible_user', 'ansible_private_key_file',
                   'bootstrap_login_user', 'ansible_python_interpreter', 'swap_size_mb',
                   'ssh_listen_ports', 'ssh_verify_ports', 'firewall_allowed_tcp_ports'}
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', alias) or alias == 'localhost':
            raise ValueError
        if not isinstance(values, dict) or not set(values) <= allowed:
            raise ValueError
        host, port, user, key = (values[k] for k in (
            'ansible_host', 'ansible_port', 'ansible_user', 'ansible_private_key_file'))
        if not isinstance(host, str) or len(host) > 253 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.:-]*', host):
            raise ValueError
        if ':' in host:
            ipaddress.IPv6Address(host)
        elif not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
                     for label in host.split('.')):
            raise ValueError
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError
        if not isinstance(user, str) or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,30}', user) or user == 'root':
            raise ValueError
        for name in ('ssh_listen_ports', 'ssh_verify_ports', 'firewall_allowed_tcp_ports'):
            if name in values:
                ports = values[name]
                if (not isinstance(ports, list) or len(ports) > 128 or
                        (not ports and name != 'firewall_allowed_tcp_ports') or
                        any(type(p) is not int or not 1 <= p <= 65535 for p in ports) or
                        len(set(ports)) != len(ports)):
                    raise ValueError
        if 'swap_size_mb' in values and (type(values['swap_size_mb']) is not int or not 0 <= values['swap_size_mb'] <= 1048576):
            raise ValueError
        if 'bootstrap_login_user' in values and (not isinstance(values['bootstrap_login_user'], str) or
                not re.fullmatch(r'[a-z_][a-z0-9_-]{0,30}', values['bootstrap_login_user'])):
            raise ValueError
        if 'ansible_python_interpreter' in values:
            if not isinstance(values['ansible_python_interpreter'], str) or not re.fullmatch(r'/[A-Za-z0-9_./-]+', values['ansible_python_interpreter']):
                raise ValueError
        return Target(alias, host, port, user, local_path(key))
    except (RegistryValidationError, KeyError, TypeError, ValueError, AttributeError):
        raise InspectionError("invalid-inventory: require one static bootstrap host and explicit managed SSH fields") from None
