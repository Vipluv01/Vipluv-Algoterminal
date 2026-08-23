"""Dev-only static server that disables caching entirely.

Not for anything but local testing: python -m http.server sends no
Cache-Control header, and this session's browser tool was aggressively
caching JS modules across page reloads regardless -- wasted real time
during development (a fixed bug kept appearing "unfixed" because the OLD
module was what actually ran). Explicit no-store headers make every
request hit disk fresh, trading away caching entirely in exchange for
never chasing a stale-cache ghost again.
"""

import http.server


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        super().end_headers()


if __name__ == "__main__":
    http.server.test(HandlerClass=NoCacheHandler, port=5173)
