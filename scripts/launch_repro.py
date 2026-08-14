"""Local 1:1 reproduction of the plugin_daemon launch checks for my_claw.

Usage (needs a py3.11+ venv with dify-plugin installed, e.g. dpinspect/.venv312):
    python scripts/launch_repro.py

Replicates, in daemon order:
1. ToolProviderConfiguration(**provider_yaml)   (yaml -> pydantic validation)
2. Full PluginRegistration (manifest + all configs + tool classes + assets)
3. Real subprocess launch: `python -m main` with INSTALL_METHOD=local,
   waiting for the manifest JSON handshake line on stdout.
"""
from __future__ import annotations

import os

os.environ.setdefault("INSTALL_METHOD", "local")

import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import yaml

from dify_plugin.config.config import DifyPluginEnv
from dify_plugin.core.plugin_registration import PluginRegistration
from dify_plugin.entities.tool import ToolProviderConfiguration

fs = yaml.safe_load(open("provider/my_claw.yaml", encoding="utf-8"))
cfg = ToolProviderConfiguration(**fs)
print("[1] provider yaml OK:", [t.identity.name for t in cfg.tools])

reg = PluginRegistration(DifyPluginEnv())
provider_name = reg.tools_configuration[0].identity.name
print("[2] full registration OK:", {k: v[1].__name__ for k, v in reg.tools_mapping[provider_name][2].items()})
print("[3] assets:", [a.filename for a in reg.files])

env = dict(os.environ)
env["INSTALL_METHOD"] = "local"
env["PYTHONIOENCODING"] = "utf-8"  # server sets this via compose shared env
p = subprocess.Popen(
    [sys.executable, "-m", "main"],
    cwd=ROOT,
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    stdin=subprocess.PIPE,
)
out_buf, err_buf = [], []
threading.Thread(target=lambda: out_buf.append(p.stdout.read()), daemon=True).start()
threading.Thread(target=lambda: err_buf.append(p.stderr.read()), daemon=True).start()
time.sleep(5)
alive = p.poll() is None
p.kill()
time.sleep(0.5)
out = (out_buf[0] if out_buf else b"").decode("utf-8", "replace")
err = (err_buf[0] if err_buf else b"").decode("utf-8", "replace")
print("[4] subprocess launch: alive=%s stdout_bytes=%d stderr_bytes=%d" % (alive, len(out), len(err)))
if err:
    print(err[:2000])
    sys.exit(1)
if not alive or not out.strip().startswith("{"):
    print("launch handshake missing manifest JSON")
    sys.exit(1)
print("ALL CHECKS PASSED")
