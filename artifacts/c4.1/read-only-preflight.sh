#!/usr/bin/env bash
# Operator-reviewed diagnostics only. NOT RUN during C4.1 preparation.
# C4.3: fixed /usr/bin/ip matches the supplied C4.2 path finding. This revision
# has not been run; target metadata/provenance still need independent review.
# No host-key discovery/TOFU. Supply independently authenticated known_hosts/key.
# Usage (workstation, output outside repository):
# C41_KNOWN_HOSTS=/absolute/verified/known_hosts C41_SSH_KEY=/absolute/login-key \
#   bash artifacts/c4.1/read-only-preflight.sh > /secure/c41-preflight.txt
# Verify both fingerprints through an independent channel before invoking.
set -euo pipefail
: "${C41_KNOWN_HOSTS:?absolute independently verified known_hosts required}"
: "${C41_SSH_KEY:?absolute existing portfolio_admin SSH key required}"
[[ "$C41_KNOWN_HOSTS" = /* && "$C41_SSH_KEY" = /* ]]
ssh-keygen -F '[132.243.166.145]:2222' -f "$C41_KNOWN_HOSTS"
# If public counterpart is absent, stop and inspect the approved login identity
# through its existing custody process. Do not print/read the private key.
ssh-keygen -lf "${C41_SSH_KEY}.pub" -E sha256
ssh -T -p 2222 -i "$C41_SSH_KEY" \
  -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
  -o UpdateHostKeys=no -o ForwardAgent=no -o ClearAllForwardings=yes \
  -o ControlMaster=no -o ControlPath=none \
  -o UserKnownHostsFile="$C41_KNOWN_HOSTS" -o GlobalKnownHostsFile=/dev/null \
  -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=2 \
  portfolio_admin@132.243.166.145 'bash -s' <<'REMOTE'
set -euo pipefail
export LC_ALL=C
# All privileged calls below have concrete read-only argv; no sudo shell.
date -u +%FT%TZ
id
uname -m
cat /etc/os-release
sudo -n /usr/bin/cat /etc/machine-id
/usr/bin/python3 -I -S -B -c 'import sys,sysconfig,sqlite3; print(sys.version); print(sys.executable); print(sysconfig.get_config_var("SOABI")); print(sqlite3.sqlite_version)'
readlink -f /usr/bin/python3
/usr/bin/docker --version
sudo -n /usr/bin/timeout 10 /usr/bin/docker --host unix:///run/docker.sock compose version --short
sudo -n /usr/bin/timeout 10 /usr/bin/docker --host unix:///run/docker.sock info --format '{{json .}}'
sudo -n /usr/bin/stat -c '%n %F %u:%g %a %d:%i %h' /run/docker.sock
sudo -n /usr/bin/timeout 10 /usr/bin/docker --host unix:///run/docker.sock container ls --all --no-trunc
sudo -n /usr/bin/timeout 10 /usr/bin/docker --host unix:///run/docker.sock volume ls
sudo -n /usr/bin/timeout 10 /usr/bin/docker --host unix:///run/docker.sock network ls --no-trunc
sudo -n /usr/bin/timeout 10 /usr/bin/docker --host unix:///run/docker.sock image ls --all --digests --no-trunc
# Network data has no application env/secrets. Every listed ID is inspected;
# timeout/denied output fails, never interpreted as an empty inventory.
networks=$(sudo -n /usr/bin/timeout 10 /usr/bin/docker --host unix:///run/docker.sock network ls --quiet --no-trunc)
for network in $networks; do
  sudo -n /usr/bin/timeout 10 /usr/bin/docker --host unix:///run/docker.sock network inspect "$network"
done
sudo -n /usr/bin/ip -j address show
sudo -n /usr/bin/ip -j -4 route show table all
sudo -n /usr/bin/ip -j -6 route show table all
sudo -n /usr/bin/ip -j -4 rule show
sudo -n /usr/bin/ip -j -6 rule show
sudo -n /usr/bin/ss -H -lntu
/usr/bin/findmnt --json --output TARGET,SOURCE,FSTYPE,OPTIONS
/usr/bin/df -B1 / /opt /srv /var/lib/docker
/usr/bin/df -i / /opt /srv /var/lib/docker
/usr/bin/free -b
/usr/bin/nproc
# Inspect all fixed destinations AND their ancestors without following symlinks.
# No directory is created. Existing contents are not read/adopted.
sudo -n /usr/bin/python3 -I -S -B -c '
import os, stat, pathlib, json
paths = ["/opt/portfolio-deployment", "/etc/systemd/system/portfolio-deployment-executor.service",
"/usr/local/lib/portfolio-deployment-bootstrap/operator_bootstrap.py",
"/usr/local/lib/portfolio-deployment-bootstrap/admin_bindings.py",
"/var/lib/portfolio-deployment-bootstrap/bundle.zip", "/etc/portfolio-deployment/operator-keys.json",
"/srv/portfolio/.manager-storage-v1", "/srv/portfolio/locks/host.lock"]
paths += ["/srv/portfolio/"+n for n in ("state", "journal", "requests", "caddy-config", "caddy-lkg", "certificates", "caddy-data", "caddy-admin", "temporary")]
seen = set()
for name in paths:
 for path in (*reversed(pathlib.Path(name).parents), pathlib.Path(name)):
  if str(path) in seen: continue
  seen.add(str(path))
  try: s = os.lstat(path)
  except FileNotFoundError:
   print(json.dumps({"path":str(path), "status":"absent"})); continue
  print(json.dumps({"path":str(path),"uid":s.st_uid,"gid":s.st_gid,"mode":oct(stat.S_IMODE(s.st_mode)),"type":stat.S_IFMT(s.st_mode),"device":s.st_dev,"inode":s.st_ino,"links":s.st_nlink}))
  if stat.S_ISLNK(s.st_mode): raise SystemExit("STOP: destination ancestry contains a symlink")
'
# Candidate tool paths: missing/multiple/symlink files require review, never repair.
for tool in /usr/bin/python3 /usr/bin/docker /usr/bin/gh /usr/bin/ssh-keygen /usr/bin/ip /usr/bin/ss \
 /usr/local/lib/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose \
 /usr/lib/docker/cli-plugins/docker-compose /usr/libexec/docker/cli-plugins/docker-compose; do
 if [[ -e "$tool" || -L "$tool" ]]; then
  sudo -n /usr/bin/stat -c '%n %F %u:%g %a %d:%i %h' "$tool"
  readlink -f "$tool"
  sudo -n /usr/bin/sha256sum "$tool"
 else
  printf 'MISSING %s\n' "$tool"
 fi
done
/usr/bin/dpkg-query -W -f='${binary:Package} ${Version} ${Architecture}\n' python3 python3-minimal libpython3-stdlib openssh-client iproute2
if [[ -x /usr/bin/gh ]]; then /usr/bin/gh --version; fi
# Stop here. No systemctl, pull/create/start, chmod/chown, firewall or DNS writes.
# Console/provider access, backup/restore drills, quota enforcement, complete tool
# dependency provenance and phase-specific attestors need independent evidence.
REMOTE
