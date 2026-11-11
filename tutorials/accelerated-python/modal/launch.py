"""Launch the tutorial and authenticated Nsight WebRTC desktops on Modal."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
ROOT = "/accelerated-computing-hub"
APP = "accelerated-python"
TUTORIAL_IMAGE = "ghcr.io/nvidia/accelerated-python-tutorial@sha256:2a5f12d784384a676a600dfc9a0254d0bc253c4a4ad622dcfdbb5e673d014fd0"
STREAMER_IMAGES = {
    "nsys": "nvcr.io/nvidia/devtools/nsight-streamer-nsys@sha256:69f43fda4cc5580fbdaa1b888b14cad33522c92de9e7732b01b7934fa10d3382",
    "ncu": "nvcr.io/nvidia/devtools/nsight-streamer-ncu@sha256:fc9d7b8133c920b4809dbdb6182a4be45764c35a076e87d563f777cbaff34141",
}


def image_for(kind):
    import modal
    if kind == "jupyter":
        image = modal.Image.from_registry(TUTORIAL_IMAGE).entrypoint([]).workdir("/")
        # Modal requires an empty volume mountpoint. Keep seed files elsewhere
        # in the image, then initialize the volume after the sandbox starts.
        image = image.run_commands("mv /accelerated-computing-hub /opt/ach-source", "mkdir /accelerated-computing-hub")
    else:
        image = modal.Image.from_registry(STREAMER_IMAGES[kind], add_python="3.12").entrypoint([])
    for name in ("runtime.py", "sync.py", "smoke.py"):
        image = image.add_local_file(HERE / name, "/opt/modal-tutorial/" + name, copy=True)
    if kind != "jupyter":
        image = image.run_commands("python /opt/modal-tutorial/runtime.py patch-streamer")
    return image


def save(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def bootstrap(sandbox, kind, config):
    process = sandbox.exec("python", "/opt/modal-tutorial/runtime.py", kind, timeout=1200)
    process.stdin.write(json.dumps(config))
    process.stdin.write_eof()
    process.stdin.drain()
    process.wait()
    if process.returncode:
        raise RuntimeError(process.stderr.read())


def wait_http(url, headers=None):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            with urlopen(Request(url, headers=headers or {}), timeout=10) as response:
                if response.status == 200:
                    return
        except (HTTPError, URLError, TimeoutError):
            pass
        time.sleep(2)
    raise TimeoutError("Service failed its HTTP readiness check")


def start(args, state):
    import base64
    import modal
    app = modal.App.lookup(APP, create_if_missing=True)
    if not state.get("services"):
        state.clear()
        state.update({"profile": os.environ.get("MODAL_PROFILE", "active"), "app_id": app.app_id,
                      "expires_at": (datetime.now(timezone.utc) + timedelta(hours=args.hours)).isoformat(),
                      "services": {}})
    save(args.state, state)
    report_config = state.get("report_sync", {})
    kinds = ["nsys", "ncu"] if args.desktops_only else ["jupyter", "nsys", "ncu"]
    for kind in kinds:
        service = state["services"].get(kind, {})
        same_reports = kind == "jupyter" or service.get("report_url") == report_config.get("report_url")
        if service.get("url") and same_reports and modal.Sandbox.from_id(service["sandbox_id"]).poll() is None:
            continue
        print(f"Starting {kind}...", flush=True)
        kwargs = {"cpu": 2, "memory": 4096, "encrypted_ports": [8080, 3478]}
        if kind == "jupyter":
            volume = modal.Volume.from_name("accelerated-python-data", create_if_missing=True, version=2)
            kwargs = {"gpu": args.gpu, "cpu": 4, "memory": 16384,
                      "encrypted_ports": [8888, 8899], "volumes": {ROOT: volume}}
        if service.get("sandbox_id"):
            modal.Sandbox.from_id(service["sandbox_id"]).terminate()
        sandbox = modal.Sandbox.create("sleep", "infinity", app=app, image=image_for(kind),
                                       timeout=int(args.hours * 3600), workdir="/", **kwargs)
        service = state["services"][kind] = {
            "sandbox_id": sandbox.object_id,
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=args.hours)).isoformat(),
        }
        state["expires_at"] = min(s.get("expires_at", service["expires_at"]) for s in state["services"].values())
        save(args.state, state)
        tunnels = sandbox.tunnels()
        if kind == "jupyter":
            token = secrets.token_urlsafe(32)
            report_config = {"report_token": secrets.token_urlsafe(32), "report_url": tunnels[8899].url}
            state["report_sync"] = report_config
            bootstrap(sandbox, "jupyter", {"token": token, "source_image": TUTORIAL_IMAGE, **report_config})
            service["url"] = tunnels[8888].url + "/lab?token=" + token
            wait_http(tunnels[8888].url + "/api", {"Authorization": "token " + token})
        else:
            password = secrets.token_urlsafe(24)
            env = {"WEB_USERNAME": "bryce", "WEB_PASSWORD": password,
                   "TURN_USERNAME": "bryce", "TURN_PASSWORD": secrets.token_urlsafe(32),
                   "SELKIES_TURN_HOST": tunnels[3478].tls_socket[0], "SELKIES_TURN_PORT": "443",
                   "SELKIES_TURN_TLS": "true", "TURN_PROTOCOL": "tcp", "TURN_PORT": "3478",
                   "HOST_IP": "0.0.0.0", "SELKIES_ADDR": "0.0.0.0", "HTTP_PORT": "8080",
                   "TURN_REALM": "localhost", "ENCODER": "vp9enc", "MAX_RESOLUTION": "1600x900"}
            bootstrap(sandbox, "streamer", {"env": env, **report_config})
            service.update({"url": tunnels[8080].url, "username": "bryce", "password": password})
            service["report_url"] = report_config.get("report_url")
            basic = base64.b64encode(f"bryce:{password}".encode()).decode()
            wait_http(service["url"], {"Authorization": "Basic " + basic})
        save(args.state, state)
        print(f"{kind} ready: {service['url'].split('?')[0]}", flush=True)
    print(f"Session credentials saved to {args.state.resolve()}")
    print("Run 'check' to test CUDA and collect both real profiler reports.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "status", "check", "stop"])
    parser.add_argument("--profile", help="Explicit Modal workspace profile")
    parser.add_argument("--state", type=Path, default=Path(".modal-session.json"))
    parser.add_argument("--gpu", default="A10", help="GPU type; A10 supports the single-GPU tutorial lessons")
    parser.add_argument("--hours", type=float, default=4)
    parser.add_argument("--desktops-only", action="store_true", help="Diagnose WebRTC without allocating a GPU")
    parser.add_argument("--resume", action="store_true", help="Resume an interrupted start from its state file")
    args = parser.parse_args()
    if args.profile:
        os.environ["MODAL_PROFILE"] = args.profile
    # Modal reads its profile when imported. Import only after applying the
    # explicit selection so an unrelated active workspace is never used.
    import modal
    state = json.loads(args.state.read_text()) if args.state.exists() else {}
    if state.get("profile") not in (None, "active", os.environ.get("MODAL_PROFILE")):
        parser.error("Session belongs to a different Modal profile; pass --profile explicitly")
    if args.command == "start":
        if not 0 < args.hours <= 24:
            parser.error("--hours must be between 0 and 24")
        if state.get("services") and not args.resume:
            parser.error("Use --resume, stop the existing session, or use another --state file")
        with modal.enable_output():
            start(args, state)
    elif args.command == "status":
        for name, service in state.get("services", {}).items():
            sandbox = modal.Sandbox.from_id(service["sandbox_id"])
            print(name, "running" if sandbox.poll() is None else "stopped", service.get("url", "starting"))
        print("Expires:", state.get("expires_at", "No session"))
    elif args.command == "check":
        if "jupyter" not in state.get("services", {}):
            parser.error("No GPU tutorial session in this state file")
        sandbox = modal.Sandbox.from_id(state["services"]["jupyter"]["sandbox_id"])
        process = sandbox.exec("python", "/opt/modal-tutorial/smoke.py", timeout=600)
        for line in process.stdout:
            print(line, end="", flush=True)
        process.wait()
        print(process.stderr.read(), end="")
        sys.exit(process.returncode)
    elif args.command == "stop":
        for name, service in list(state.get("services", {}).items()):
            modal.Sandbox.from_id(service["sandbox_id"]).terminate()
            del state["services"][name]
            save(args.state, state)
            print("Stopped", name)


if __name__ == "__main__":
    main()
