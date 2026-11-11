# Accelerated Python on Modal

This launcher runs the tutorial in a GPU Sandbox and runs the NVIDIA Nsight
Systems and Nsight Compute desktops in two CPU Sandboxes. All three use
authenticated HTTPS endpoints. The desktops use WebRTC with TURN over TLS on
port 443, so no public UDP ports or local TURN server are required.

## Start a session

Use Python 3.10 or newer on your computer. From this directory:

```console
python -m pip install -r requirements.txt
python launch.py start --profile YOUR_MODAL_PROFILE --gpu A10 --hours 4
python launch.py status --profile YOUR_MODAL_PROFILE
```

If you have not authenticated Modal, first run `python -m modal setup`.
Select your intended workspace explicitly with `--profile`; the launcher does
not change your active Modal profile. Initial image imports can take several
minutes. GPU allocation can also take time.

The launcher writes `.modal-session.json` containing the Jupyter token URL,
desktop URLs and login credentials, sandbox IDs, and expiration time. Keep this
file private. Open the Jupyter URL and log into each desktop using its saved
username and password. Use the clean desktop URL; putting Basic Auth credentials
in the URL can break the streamer's browser requests.

A10 supports the single-GPU CuPy, CUDA Python, CUDA Tile, and profiler lessons
in the pinned tutorial. `--gpu B200` selects Blackwell hardware when needed.
Distributed multi-GPU notebooks need additional GPUs and are outside this
single-GPU launcher's scope.

If a launch is interrupted, repeat the command with `--resume` and the same
state file. `--state PATH` selects a different session file. Avoid simultaneously
running multiple GPU sessions against the same tutorial volume.

## Profile and inspect

```console
python launch.py check --profile YOUR_MODAL_PROFILE
```

This executes a CuPy calculation, collects actual `.nsys-rep` and `.ncu-rep`
reports, and checks exported CUDA kernel activity and GPU metrics. A successful
HTTP response or desktop connection alone does not pass this check.

The test files appear under:

```text
/accelerated-computing-hub/tutorials/accelerated-python/notebooks/modal-reports/
```

In either desktop, use **File → Open** to open the corresponding report there.
New or changed reports and their Python/CUDA sources copy to both desktops
automatically after they are stable across two five-second polls. Reopen a
report after overwriting it. The desktop copies are for viewing; edit sources
and notebooks in Jupyter.

The tutorial's **Python 3 (Nsight Systems)** and **Python 3 (Nsight Compute)**
kernels remain available. The launcher points them at the real profiler CLIs
instead of the Docker Compose capability wrappers. CPU performance sampling and
CPU context-switch tracing are disabled for Nsight Systems; CUDA/NVTX/OS runtime
tracing remains enabled. Nsight Compute leaves GPU clocks unchanged.

## Persistence and shutdown

Notebook edits, data, and reports persist in the Modal v2 Volume
`accelerated-python-data`, mounted at `/accelerated-computing-hub`. Initial
contents come from the pinned tutorial image. Relaunches preserve existing
files; they do not silently overwrite student work with newer image contents.

```console
python launch.py stop --profile YOUR_MODAL_PROFILE
```

Stopping terminates only the Sandboxes listed in the session file and preserves
the volume. The Sandboxes also expire automatically after `--hours` (default 4,
maximum 24). GPU compute is billed while the GPU Sandbox is running, including
when Jupyter is idle. The two desktops use CPU compute only. No local process
needs to stay running after startup completes.

## WebRTC troubleshooting

Open the streamer's side panel and enable **Force relay connection**, then
reload. Both GUI images are patched for Modal's TLS tunnel: the external TURN
port is 443, the internal listener remains 3478, and Pion advertises the
container's reachable relay address. The browser retains remote host candidates
while forcing its own candidates through TURN. The patched JavaScript is cache
busted.

`python launch.py start --desktops-only --profile YOUR_MODAL_PROFILE` tests the
desktops without allocating a GPU. It does not start Jupyter or collect profiles.
Use a separate `--state` file for diagnostic sessions.

## Versions and validation

`launch.py` pins the NVIDIA tutorial and streamer images by digest. The tutorial
CLI versions are Nsight Systems 2026.1.1 and Nsight Compute 2026.1.1; the desktop
versions are 2026.1.1 and 2026.2.1 respectively. The newer Compute desktop can
read the tutorial CLI's reports. Updating an image requires rechecking the
streamer patch anchors and running the real profiling check.

The report service's authentication and file boundary tests can be run locally:

```console
python -m pip install pytest
python -m pytest -q test_sync.py
```

References: [Modal Sandbox networking](https://modal.com/docs/guide/sandbox-networking),
[Modal Volumes](https://modal.com/docs/guide/volumes), and
[NVIDIA Nsight Streamer](https://docs.nvidia.com/nsight-operator/NsightStreamer/index.html).
