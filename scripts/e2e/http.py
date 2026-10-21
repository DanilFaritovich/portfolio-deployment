"""Internal HTTP helper. Parent enforces a hard wall-clock/process/output bound."""
import base64
import http.client
import json
import sys


def main():
    ip, port, path, timeout, method, body, host = sys.argv[1:]
    connection = http.client.HTTPConnection(ip, int(port), timeout=float(timeout))
    try:
        connection.request(method, path, body=base64.b64decode(body) if body else None,
                           headers={"Content-Type": "application/json", **({"Host": host} if host else {})})
        response = connection.getresponse()
        data = response.read(65537)
        if len(data) > 65536:
            return 2
        print(json.dumps([response.status, base64.b64encode(data).decode("ascii")]))
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, http.client.HTTPException):
        raise SystemExit(2) from None
