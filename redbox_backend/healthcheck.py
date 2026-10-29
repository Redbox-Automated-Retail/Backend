"""Container healthcheck: `python -m redbox_backend.healthcheck URL...` exits 0 when any URL answers 2xx.

Certificates are not verified (the services use the local CA; this only checks that they answer).
"""
import ssl
import sys
import urllib.request


def main(urls):
    ctx = ssl._create_unverified_context()
    for url in urls:
        try:
            with urllib.request.urlopen(url, timeout=4, context=ctx) as r:
                if 200 <= r.status < 300:
                    return 0
        except Exception as e:
            print(f"{url}: {e}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
