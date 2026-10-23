"""Fixed installation-only Caddy endpoint; never supplied by an operation."""

HOST_DIRECTORY = '/srv/portfolio/caddy-admin'
CONTAINER_DIRECTORY = '/run/portfolio-caddy-admin'
SOCKET_NAME = 'admin.sock'
HOST_SOCKET = HOST_DIRECTORY + '/' + SOCKET_NAME
CONTAINER_ADDRESS = 'unix/' + CONTAINER_DIRECTORY + '/' + SOCKET_NAME + '|0600'
HOST_ADDRESS = 'unix/' + HOST_SOCKET + '|0600'
