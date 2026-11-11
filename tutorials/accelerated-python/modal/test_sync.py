"""Exercise the report service's authentication and file boundary."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

spec = importlib.util.spec_from_file_location("report_sync", Path(__file__).with_name("sync.py"))
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "ROOT", tmp_path.resolve())
    monkeypatch.setenv("ACH_REPORT_TOKEN", "test-token")
    (tmp_path / "profile.ncu-rep").write_bytes(b"test report bytes")
    (tmp_path / "private.txt").write_text("do not publish")
    http = sync.ThreadingHTTPServer(("127.0.0.1", 0), sync.Reports)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{http.server_port}"
    http.shutdown()
    http.server_close()
    thread.join()


def fetch(url, token="test-token"):
    return urlopen(Request(url, headers={"Authorization": "Bearer " + token}), timeout=5)


def test_authentication_and_index(server):
    with pytest.raises(HTTPError) as failure:
        fetch(server + "/index", "wrong-token")
    assert failure.value.code == 401
    with fetch(server + "/index") as response:
        assert set(json.load(response)) == {"profile.ncu-rep"}


def test_report_bytes(server):
    with fetch(server + "/file?path=profile.ncu-rep") as response:
        assert response.read() == b"test report bytes"


@pytest.mark.parametrize("path", ["../outside.ncu-rep", "/etc/passwd", "private.txt", "missing.nsys-rep"])
def test_reject_disallowed_files(server, path):
    with pytest.raises(HTTPError) as failure:
        fetch(server + "/file?path=" + sync.quote(path, safe=""))
    assert failure.value.code == 404


def test_follow_transfers_completed_report(server, tmp_path):
    # Separate processes model the GPU's report service and a desktop's local
    # copy. The destination must be outside the source tree being indexed.
    with tempfile.TemporaryDirectory(dir=tmp_path.parent) as destination:
        code = (
            "import importlib.util,sys; from pathlib import Path; "
            "spec=importlib.util.spec_from_file_location('sync',sys.argv[1]); "
            "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); "
            "m.ROOT=Path(sys.argv[2]); m.follow()"
        )
        env = {**os.environ, "ACH_REPORT_URL": server, "ACH_REPORT_TOKEN": "test-token"}
        process = subprocess.Popen(
            [sys.executable, "-c", code, str(Path(__file__).with_name("sync.py")), destination],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            report = Path(destination) / "profile.ncu-rep"
            deadline = time.monotonic() + 20
            while not report.exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            assert report.read_bytes() == b"test report bytes"
            assert not (Path(destination) / "private.txt").exists()
            assert not list(Path(destination).glob("*.downloading"))
        finally:
            process.terminate()
            process.wait(timeout=5)
