import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from automation_doctor.checks import CheckContext
from automation_doctor.runner import RunRecord


def make_run(exit_code=0, started_at=None, stdout="", stderr="", timed_out=False, launch_error=""):
    now = time.time()
    return RunRecord(job_name="t", run_id="r1", mode="run", command="x", cwd=".",
                     started_at=now - 5 if started_at is None else started_at, finished_at=now,
                     exit_code=exit_code, timed_out=timed_out, stdout=stdout, stderr=stderr,
                     launch_error=launch_error)


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def ctx(self, **run_kwargs):
        return CheckContext(cwd=self.dir, run=make_run(**run_kwargs))


class JsonServer:
    """Tiny local HTTP server. ``routes`` maps path -> (status, body or callable(request_json) -> body)."""

    def __init__(self, routes):
        self.routes, self.requests = routes, []

    def __enter__(self):
        outer = self

        class H(BaseHTTPRequestHandler):
            def _handle(self):
                n = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(n)) if n else None
                outer.requests.append((self.path, payload))
                status, body = outer.routes.get(self.path.split("?")[0], (404, {"error": "nf"}))
                if callable(body):
                    body = body(payload)
                raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = _handle

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
