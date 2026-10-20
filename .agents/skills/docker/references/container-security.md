# Container Least-Privilege Security

Read when creating or changing production Dockerfile/Compose runtime identity, permissions, Linux capabilities, writable filesystems, mounts, isolation, or resource limits. This reference concerns application containers; host-level Docker access belongs to the Ansible Docker-host reference.

## Per-service security review

- Identify each service's actual execution needs: user/group, listening port, writable paths, network peers, mounts, and process/resource requirements. Apply restrictions per service, not one untested setting to every image.
- Prefer a non-root runtime for application processes using `USER` in the final Dockerfile stage, a compatible Compose `user:`, or an upstream image that safely drops privileges. Check effective runtime identity, not only the presence of a Dockerfile instruction.
- Avoid a blanket UID override for official database/proxy images that need a documented initialization or ownership step. Preserve supported entrypoints and document any required elevated startup behavior.
- Grant correct ownership to immutable application artifacts and intended writable directories at build time or through managed volume permissions. Do not use `chmod 777` or a permanent root runtime to work around ownership mistakes.

## Capabilities and privilege escalation

For compatible Linux application services:

- Start with `cap_drop: [ALL]` and add only a specifically required capability with `cap_add`; justify any addition.
- Use `security_opt: [no-new-privileges:true]` to prevent privilege escalation through exec transitions where supported.
- Do not set `privileged: true` or attach host PID/IPC/network namespaces, host devices, or broad kernel permissions merely to fix an application issue.
- Do not mount `/var/run/docker.sock` into normal application containers. Socket access can grant Docker-daemon/host-level control, even when the container process itself is non-root. If a dedicated deployment/administration service genuinely needs it, review access separately.
- Do not weaken default seccomp/AppArmor/SELinux protections without a concrete, reviewed compatibility requirement.

## Filesystem and mounts

- Prefer `read_only: true` for stateless application containers where practical.
- Add only explicit writable locations such as `tmpfs: [/tmp]` for temporary files or correctly permissioned volumes for durable state. Set the appropriate ownership/permissions for the runtime UID and confirm the service can actually write where needed.
- Keep databases and other stateful images on managed persistent volumes. Do not impose a read-only root filesystem or forced UID when it breaks documented startup, upgrade, or data management.
- Avoid writable bind mounts of the host filesystem, particularly project source, Docker directories, host credentials, and sensitive system paths, in production. Prefer narrow, read-only mounts for configuration.
- Prefer stdout/stderr for application logs; if file logging is required, identify and provision only its writable path.

## Resource and network boundaries

- When meaningful for the deployment, define appropriate Compose resource controls such as `pids_limit`, memory, and CPU limits. Choose values from observed workload and hosting constraints, not universal fixed numbers; validate supported Compose/runtime behavior.
- Continue to enforce the public-gateway/internal-service model from `references/production-networking.md`. A process running as non-root is not a reason to publish its port to the host.
- Running as non-root, dropping capabilities, and a read-only root filesystem reduce risk but do not replace correct secret handling, security updates, network segmentation, or application authorization.

## Illustrative Compose configuration

This is a starting point for a compatible *stateless* backend, not a required literal template for databases or reverse proxies:

```yaml
services:
  backend:
    image: example/backend:1.0.0
    user: "10001:10001"
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    read_only: true
    tmpfs:
      - /tmp
    pids_limit: 100
    # No public host port: route traffic through the existing gateway.
```

Adapt the user to the actual final image and mount ownership; supply only required exceptions, with a concise reason in the Compose configuration or related deployment documentation. A fixed `pids_limit` may be unsuitable for multi-worker processes.

## Verification

1. Run `docker compose config` and confirm that production does not accidentally enable host publishing or privileged modes.
2. If the local/container environment is available, check the actual runtime user, required capability/security settings, effective writable paths, and expected health/readiness behavior.
3. Exercise service startup, database migrations (when relevant), file writes, graceful shutdown, and gateway connectivity after enabling restrictions.
4. If a constraint prevents a service from starting, diagnose the specific denied operation; grant only the smallest necessary exception and record why. Do not globally disable protections.
5. Keep validation output summary-first, and do not run checks against a live production host without separate authorization.
