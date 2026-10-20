# Docker Production Networking Reference

Read this reference when changing production exposure, reverse proxy routing, service networks, frontend/backend topology, or database exposure.

## Public gateway model

For browser-based full-stack applications, expose only the public gateway/reverse proxy whenever practical.

Typical flow:

```text
Internet / Browser
-> public gateway
   -> static frontend
   -> /api/* -> backend
                  -> database
                  -> Redis when enabled
```

Backend/database services should normally stay on internal Docker networks and not publish host ports in production.

Browsers cannot call internal Docker hostnames directly; public API traffic reaches backend services through the gateway.

Caddy or Nginx are typical gateway choices.

## Edge anti-flood

When Nginx is the public FastAPI gateway, place generic HTTP anti-flood at Nginx for the public API prefix.

Use trusted client IP, configured normal rate/burst, and HTTP 429.

If Nginx sits behind another trusted proxy/load balancer, configure real-IP handling correctly before using client address as the limiter key.

Do not expose backend ports publicly just to implement rate limiting.

## Development vs production ports

Development may expose backend/database ports when needed for debugging or tooling.

Do not copy development exposure into production automatically.

## Compose networks

Use explicit networks when they improve isolation.

A common model has:

- public/gateway network;
- internal application network.

Database services normally join only the internal network. Backend joins only networks required for gateway/database communication.

## Frontend

For production Vue/static builds:

- build assets in a dedicated build stage;
- serve compiled assets from the gateway/static server;
- route API requests to the internal backend.

Do not ship the full Node development toolchain in the final static runtime image unless required.

## Backend

Backend containers should:

- install only runtime dependencies;
- use the production server command;
- expose their port internally;
- avoid public host publishing when a gateway exists;
- terminate gracefully.

## Database

PostgreSQL should:

- use persistent storage;
- use environment/secrets for configuration;
- remain internal in production;
- expose a meaningful health check when startup dependencies require it.
