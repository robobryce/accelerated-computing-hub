"""Restore the tutorial's embedded Nsight launchers over Modal TLS tunnels."""

import base64
import json
from pathlib import Path
from urllib.parse import urlsplit

DESKTOPS = Path("/tmp/modal-nsight-desktops.json")
SERVICES = {"nsys": 8080, "ncu": 8081}


def upstream_headers(name):
    desktop = json.loads(DESKTOPS.read_text())[name]
    host = urlsplit(desktop["url"]).hostname
    credentials = base64.b64encode(f"{desktop['username']}:{desktop['password']}".encode()).decode()
    # Jupyter authenticates the browser. The separate desktop credentials stay
    # on the server, and notebook cookies never need to reach the desktop.
    return {"Host": host, "Authorization": "Basic " + credentials, "Cookie": ""}


def servers(tutorial_servers):
    def headers_for(name):
        # No positional arguments: server-proxy injects callable arguments.
        def headers():
            return upstream_headers(name)
        return headers

    # Keep the tutorial's entries, icons, categories, and embedded-tab behavior.
    # Only authenticate the HTTPS connections to the separate Modal desktops.
    result = {name: dict(value) for name, value in tutorial_servers.items()}
    for name in SERVICES:
        result[name]["request_headers_override"] = headers_for(name)
    return result
