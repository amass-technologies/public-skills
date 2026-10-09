"""Acceptance test 6 for the watchlist monitor: cursor binding.

The monitor freezes each chunk's id set for the whole sweep, so it never changes the
set mid-sweep on its own. This harness forces the case anyway: it shrinks page size
to one so the sweep needs a cursor, then drops one amassId from the first request
that carries a cursor, as if the watchlist had been edited mid-sweep. The API answers
400; the monitor must say so clearly and restart the chunk from its stored since.

    python3 tests/acceptance/cursor_binding.py run <watchlist> [monitor flags]
"""

import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "skills", "amass-watchlist-monitor", "scripts"))
import monitor  # noqa: E402

monitor.PAGE_LIMIT = 1
_send = monitor.Client._send
_done = []


def send(self, method, url, body):
    if "cursor=" in url and not _done:
        _done.append(True)
        parts = url.split("&")
        last = max(i for i, p in enumerate(parts) if p.startswith("amassId="))
        dropped = parts.pop(last)
        url = "&".join(parts)
        print(f"[harness] dropping {dropped} from the next page request: the id set changes mid-sweep", file=sys.stderr)
    return _send(self, method, url, body)


monitor.Client._send = send
sys.exit(monitor.main(sys.argv[1:]))
