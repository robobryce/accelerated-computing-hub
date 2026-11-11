"""Copy completed profiles to the desktops without shared-volume reload races."""

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import Request, urlopen

ROOT = Path("/accelerated-computing-hub")
SUFFIXES = {".nsys-rep", ".ncu-rep", ".py", ".cu", ".cuh", ".ipynb"}


def allowed_path(relative):
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT) or path.suffix not in SUFFIXES or ".git" in path.parts:
        raise ValueError("Invalid report path")
    return path


class Reports(BaseHTTPRequestHandler):
    def do_GET(self):
        expected = "Bearer " + os.environ["ACH_REPORT_TOKEN"]
        if not hmac.compare_digest(self.headers.get("Authorization", ""), expected):
            self.send_error(401)
            return
        url = urlsplit(self.path)
        if url.path == "/index":
            files = {}
            for path in ROOT.rglob("*"):
                if path.is_file() and path.suffix in SUFFIXES and ".git" not in path.parts:
                    info = path.stat()
                    files[path.relative_to(ROOT).as_posix()] = [info.st_size, info.st_mtime_ns]
            data = json.dumps(files).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif url.path == "/file":
            try:
                path = allowed_path(parse_qs(url.query)["path"][0])
                handle = path.open("rb")
            except (ValueError, KeyError, OSError):
                self.send_error(404)
                return
            with handle:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(os.fstat(handle.fileno()).st_size))
                self.end_headers()
                while block := handle.read(1024 * 1024):
                    self.wfile.write(block)
        else:
            self.send_error(404)

    def log_message(self, *_):
        pass


def follow():
    base = os.environ["ACH_REPORT_URL"].rstrip("/")
    headers = {"Authorization": "Bearer " + os.environ["ACH_REPORT_TOKEN"]}
    copied, previous = {}, {}
    while True:
        try:
            with urlopen(Request(base + "/index", headers=headers), timeout=30) as response:
                current = json.load(response)
            for name, version in current.items():
                # Require an unchanged size and mtime across two polls. Large
                # report files are never installed in-place in a running GUI.
                if previous.get(name) != version or copied.get(name) == version:
                    continue
                target = allowed_path(name)
                target.parent.mkdir(parents=True, exist_ok=True)
                temp = target.with_name(target.name + ".downloading")
                request = Request(base + "/file?path=" + quote(name, safe=""), headers=headers)
                with urlopen(request, timeout=180) as response, temp.open("wb") as output:
                    while block := response.read(1024 * 1024):
                        output.write(block)
                if temp.stat().st_size == version[0]:
                    temp.replace(target)
                    target.chmod(0o644)
                    copied[name] = version
                else:
                    temp.unlink(missing_ok=True)
            previous = current
        except Exception as error:
            # Do not print request headers or bearer credentials.
            print(f"Report synchronization retry: {type(error).__name__}", flush=True)
        time.sleep(5)


if __name__ == "__main__":
    if sys.argv[1] == "serve":
        ThreadingHTTPServer(("0.0.0.0", 8899), Reports).serve_forever()
    elif sys.argv[1] == "follow":
        follow()
