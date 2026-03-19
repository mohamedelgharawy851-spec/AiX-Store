#!/usr/bin/env python3
import sys
import urllib.request


HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "en-US,en;q=0.9",
}


def main() -> int:
    if len(sys.argv) < 2:
        print("Missing URL argument", file=sys.stderr)
        return 1

    request = urllib.request.Request(sys.argv[1], headers=HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8", "ignore")

    sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
