"""Produce real CUDA profiles; a running GUI alone is not a profiling test."""

import json
import csv
import io
import os
from pathlib import Path
import subprocess
import sys

reports = Path("/accelerated-computing-hub/tutorials/accelerated-python/notebooks/modal-reports")
reports.mkdir(parents=True, exist_ok=True)
program = reports / "vector_add.py"
program.write_text("""import cupy as cp
x = cp.arange(1048576, dtype=cp.float32)
y = x * x + 1
cp.cuda.Stream.null.synchronize()
assert float(y[10]) == 101.0
print('CUDA calculation passed')
""")
commands = {
    "cuda": ["python", str(program)],
    "nsys": [os.environ["ACH_NSYS_PATH"], "profile", "--force-overwrite=true", "--sample=none", "--cpuctxsw=none", "--trace=cuda,nvtx", "-o", str(reports / "vector_add"), "python", str(program)],
    "ncu": [os.environ["ACH_NCU_PATH"], "--force-overwrite", "--set", "basic", "--clock-control", "none", "--launch-count", "1", "-o", str(reports / "vector_add"), "python", str(program)],
}
results = {}
for extension in (".nsys-rep", ".ncu-rep", ".sqlite"):
    (reports / ("vector_add" + extension)).unlink(missing_ok=True)
for name, command in commands.items():
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
        results[name] = {"returncode": result.returncode, "output": result.stdout + result.stderr}
    except subprocess.TimeoutExpired:
        results[name] = {"returncode": -1, "output": "Timed out after 180 seconds"}
    print(name, json.dumps(results[name]), flush=True)
for name, extension in (("nsys", ".nsys-rep"), ("ncu", ".ncu-rep")):
    path = reports / ("vector_add" + extension)
    results[name]["report_bytes"] = path.stat().st_size if path.exists() else 0
    results[name]["passed"] = results[name]["returncode"] == 0 and results[name]["report_bytes"] > 0
    if results[name]["passed"]:
        command = ([os.environ["ACH_NSYS_PATH"], "stats", "--report", "cuda_gpu_kern_sum", "--format", "csv", str(path)]
                   if name == "nsys" else [os.environ["ACH_NCU_PATH"], "--import", str(path), "--csv", "--page", "raw"])
        exported = subprocess.run(command, capture_output=True, text=True, timeout=120)
        (reports / (name + "-kernel-summary.csv")).write_text(exported.stdout)
        if name == "nsys":
            results[name]["kernel_activity"] = "cupy_arange" in exported.stdout
        else:
            rows = list(csv.DictReader(io.StringIO(exported.stdout)))
            results[name]["kernel_activity"] = any(
                "cupy_arange" in row.get("Kernel Name", "") and row.get("gpu__time_duration.sum", "") not in ("", "0", "n/a")
                for row in rows
            )
        results[name]["passed"] &= exported.returncode == 0 and results[name]["kernel_activity"]
(reports / "verification.json").write_text(json.dumps(results, indent=2))
print(json.dumps(results, indent=2), flush=True)
sys.exit(0 if results["cuda"]["returncode"] == 0 and all(results[name]["passed"] for name in ("nsys", "ncu")) else 1)
