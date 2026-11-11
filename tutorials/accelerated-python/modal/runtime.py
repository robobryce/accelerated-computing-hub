"""Container startup helpers for the Accelerated Python Modal session."""

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import signal
import subprocess
import sys
from urllib.parse import urlsplit

ROOT = Path("/accelerated-computing-hub")
NOTEBOOKS = ROOT / "tutorials/accelerated-python/notebooks"


def patch_streamer():
    """Adapt the installed NVIDIA image without vendoring its implementation."""
    for path in Path("/setup").glob("entrypoint*.sh"):
        text = path.read_text().replace("set -euxo pipefail", "set -euo pipefail")
        # Modal's control interpreter must not shadow NVIDIA's Python, which
        # owns the GUI's GI, storage, and initial-connection modules.
        text = text.replace("python3 /setup/", "/usr/bin/python3 /setup/")
        path.write_text(text)
    path = Path("/setup/entrypoint-selkies-gstreamer.sh")
    text = path.read_text()
    old = "export SELKIES_TURN_PORT=${TURN_PORT}"
    if old not in text:
        raise RuntimeError("Unsupported Streamer entrypoint: TURN port assignment changed")
    path.write_text(text.replace(old, "export SELKIES_TURN_PORT=${SELKIES_TURN_PORT:-${TURN_PORT}}"))
    path = Path("/opt/gst-web/webrtc.js")
    text = path.read_text()
    old = 'if (this.forceTurn && JSON.stringify(icecandidate).indexOf("relay") < 0)'
    if text.count(old) != 1:
        raise RuntimeError("Unsupported Streamer WebRTC client: ICE filter changed")
    # The browser still uses iceTransportPolicy=relay. Its TURN allocation
    # must be able to reach the desktop's host candidates on the same machine.
    path.write_text(text.replace(old, "if (false /* Modal: accept remote host candidates */)"))
    path = Path("/opt/gst-web/index.html")
    path.write_text(re.sub(r"webrtc\.js\?ts=[^\"']+", "webrtc.js?ts=modal-relay-v2", path.read_text()))


def start_streamer(config):
    env = config["env"]
    os.environ.update(env)
    # Pion's public-ip is the relay address advertised to the desktop. Both
    # processes are local; 127.0.0.1 is rejected by ICE implementations.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
        route.connect(("8.8.8.8", 80))
        address = route.getsockname()[0]
    path = Path("/etc/webrtc/supervisord.conf")
    path.write_text(path.read_text().replace("-public-ip 127.0.0.1", f"-public-ip {address}"))
    home = Path("/home/nvidia")
    with (home / ".bashrc").open("a") as handle:
        for key, value in env.items():
            handle.write(f"export {key}={shlex.quote(value)}\n")
    subprocess.Popen(
        ["bash", "/setup/entrypoint.sh"],
        stdin=subprocess.DEVNULL,
        stdout=Path("/tmp/streamer.log").open("w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    if config.get("report_url"):
        start_sync("follow", config)


def start_sync(mode, config):
    env = os.environ.copy()
    env["ACH_REPORT_TOKEN"] = config["report_token"]
    env["ACH_REPORT_URL"] = config.get("report_url", "")
    subprocess.Popen(
        [sys.executable, "/opt/modal-tutorial/sync.py", mode],
        env=env, stdin=subprocess.DEVNULL,
        stdout=Path("/tmp/report-sync.log").open("w"),
        stderr=subprocess.STDOUT, start_new_session=True,
    )


def configure_launchers(desktops):
    from jupyter_launchers import DESKTOPS, SERVICES
    temporary = DESKTOPS.with_suffix(".tmp")
    temporary.write_text(json.dumps(desktops))
    temporary.chmod(0o600)
    temporary.replace(DESKTOPS)
    for name, port in SERVICES.items():
        url = urlsplit(desktops[name]["url"])
        if url.scheme != "https" or not url.hostname.endswith(".modal.host"):
            raise ValueError("Expected an HTTPS Modal desktop tunnel")
        command = ["socat", f"TCP-LISTEN:{port},bind=127.0.0.1,reuseaddr,fork",
                   f"OPENSSL:{url.hostname}:443,snihost={url.hostname},verify=1,cafile=/etc/ssl/certs/ca-certificates.crt"]
        pid_file = Path(f"/tmp/modal-{name}-bridge.json")
        if pid_file.exists():
            previous = json.loads(pid_file.read_text())
            try:
                running = Path(f"/proc/{previous['pid']}/cmdline").read_bytes().decode().rstrip("\0").split("\0")
                if running == command:
                    continue
                if running == previous["command"]:
                    os.kill(previous["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
            except FileNotFoundError:
                pass
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=Path(f"/tmp/modal-{name}-bridge.log").open("w"),
                                   stderr=subprocess.STDOUT, start_new_session=True)
        pid_file.write_text(json.dumps({"pid": process.pid, "command": command}))


def start_jupyter(config):
    """Initialize the persistent checkout once; never overwrite student work."""
    source = Path("/opt/ach-source")
    marker = ROOT / ".modal-initialized"
    if not marker.exists():
        # The volume is dedicated to this tutorial. Copy missing files only so
        # interrupted initialization can be retried without destroying edits.
        for path in source.rglob("*"):
            relative = path.relative_to(source)
            if ".git" in relative.parts:
                continue
            target = ROOT / relative
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        marker.write_text(config["source_image"] + "\n")
    os.chdir(NOTEBOOKS)
    # The Compose wrappers request Linux capabilities that are not available
    # in a Modal sandbox. Use the installed profiler CLIs directly.
    wrappers = Path("/opt/modal-profiler-bin")
    wrappers.mkdir(exist_ok=True)
    for name in ("nsys", "ncu"):
        destination = wrappers / name
        if not destination.exists():
            destination.symlink_to(os.environ[f"ACH_{name.upper()}_PATH"])
    os.environ["PATH"] = f"{wrappers}:" + os.environ["PATH"]
    os.environ["JUPYTER_TOKEN"] = config["token"]
    # Keep the Nsightful kernelspec intact: it uses `nsys launch`, which does
    # not accept sampling flags. Its %%nsys magic already disables CPU sample
    # and context-switch collection on the subsequent `nsys start` command.
    # Run the actual tutorial entrypoint. It installs its server-config and
    # IPython links and generates all tutorial JupyterLab user settings.
    # Modal profiling requires root; this is an upstream-supported ACH_USER.
    os.environ["ACH_USER"] = "root"
    overlay = Path("/opt/modal-jupyter")
    overlay.mkdir(exist_ok=True)
    os.environ["JUPYTER_CONFIG_PATH"] = str(overlay)
    config_path = overlay / "jupyter_server_config.py"
    config_path.write_text(
        "import os\n"
        "import sys\n"
        "sys.path.insert(0, '/opt/modal-tutorial')\n"
        "from jupyter_launchers import servers\n"
        "load_subconfig('/accelerated-computing-hub/brev/jupyter-server-config.py')\n"
        "c = get_config()\n"
        "c.ServerProxy.servers = servers(c.ServerProxy.servers)\n"
        "c.IdentityProvider.token = os.environ['JUPYTER_TOKEN']\n"
    )
    subprocess.Popen(
        [str(ROOT / "brev/entrypoint.bash"), "jupyter"],
        stdin=subprocess.DEVNULL,
        stdout=Path("/tmp/jupyter.log").open("w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    start_sync("serve", config)


def main():
    if sys.argv[1] == "patch-streamer":
        patch_streamer()
        return
    config = json.load(sys.stdin)
    {"jupyter": start_jupyter, "streamer": start_streamer,
     "launchers": configure_launchers}[sys.argv[1]](config)


if __name__ == "__main__":
    main()
