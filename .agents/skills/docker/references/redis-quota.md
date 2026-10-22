# Docker Redis Quota Reference

Read this reference when Docker/Compose must provide Redis for distributed application quota/cache state.

Redis should:

- be reachable by backend containers on an internal network;
- avoid public production host ports unless operations explicitly require them;
- have a health check;
- expose connection configuration through an environment setting such as `REDIS_URL`.

Backend startup should depend on Redis readiness when Redis is required to serve configured use cases.

Do not store authoritative distributed quota state in backend container memory.

Persistence is not automatically required for short-lived rate-limit/quota state; choose it based on project requirements.

For rolling/sliding quota implementation, the critical consume operation must be atomic.

A Redis-side operation may:

1. remove expired entries;
2. count current usage;
3. reject if a limit is reached;
4. calculate retry-after;
5. record allowed usage;
6. maintain useful expiry.

Do not replace required rolling-window semantics with naive fixed-window `INCR + EXPIRE`.

Quota keys should clearly separate quota scope/resource and client identity.

Endpoints intended to share quota must use the same quota scope.
