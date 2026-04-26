import os
import stat
import subprocess
import sys
from pathlib import Path

from nputop.api import ResourceMetricCollector, take_snapshots
from nputop.api import libascend
from nputop.api.device import Device
from nputop.select import select_devices


RAW_NPUSMI = """
+------------------------------------------------------------------------------------------------+
| npu-smi 23.0.2.1                 Version: 23.0.2.1                                             |
+---------------------------+---------------+----------------------------------------------------+
| NPU   Name                | Health        | Power(W)    Temp(C)           Hugepages-Usage(page)|
| Chip                      | Bus-Id        | AICore(%)   Memory-Usage(MB)  HBM-Usage(MB)        |
+===========================+===============+====================================================+
| 0     910B2C              | OK            | 88.6        51                0    / 0             |
| 0                         | 0000:5A:00.0  | 0           0    / 0          1024 / 65536         |
+===========================+===============+====================================================+
| 1     910B2C              | OK            | 99.6        50                0    / 0             |
| 0                         | 0000:19:00.0  | 0           0    / 0          2048 / 65536         |
+===========================+===============+====================================================+
+---------------------------+---------------+----------------------------------------------------+
| NPU     Chip              | Process id    | Process name             | Process memory(MB)      |
+===========================+===============+====================================================+
| No running processes found in NPU 0                                                            |
+===========================+===============+====================================================+
"""


def reset_libascend_cache():
    libascend._CACHE.clear()
    libascend._IDX.clear()
    libascend._npu_chip_phy.clear()
    libascend._cache_ts = 0.0
    libascend._last_update_wall_ts = 0.0
    libascend._last_update_duration = libascend.NA
    libascend._last_update_error = libascend.NA
    libascend._DRIVER_VERSION = None
    libascend._CANN_VERSION = None


def seed_libascend_cache():
    reset_libascend_cache()
    libascend._update_cache(RAW_NPUSMI)


def test_device_public_compat_methods(monkeypatch):
    seed_libascend_cache()
    monkeypatch.setenv("ASCEND_RT_VISIBLE_DEVICES", "1,0")
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)

    assert Device.all() == [Device(0), Device(1)]
    assert Device.parse_cuda_visible_devices() == [1, 0]
    assert Device.normalize_cuda_visible_devices() == "ASCEND-01,ASCEND-00"

    visible_devices = Device.from_cuda_visible_devices()
    assert [device.index for device in visible_devices] == [1, 0]
    assert [device.cuda_index for device in visible_devices] == [0, 1]
    assert Device.cuda.all() == visible_devices

    device = Device(0)
    assert device.mig_devices() == []
    assert device.to_leaf_devices() == [device]


def test_invalid_visible_devices_return_empty(monkeypatch):
    seed_libascend_cache()
    monkeypatch.setenv("ASCEND_RT_VISIBLE_DEVICES", "0,0")
    assert Device.parse_cuda_visible_devices() == []

    monkeypatch.setenv("ASCEND_RT_VISIBLE_DEVICES", "9")
    assert Device.parse_cuda_visible_devices() == []

    monkeypatch.setenv("ASCEND_RT_VISIBLE_DEVICES", "")
    assert Device.parse_cuda_visible_devices() == []


def test_snapshot_select_and_collector_public_paths(monkeypatch):
    seed_libascend_cache()
    monkeypatch.delenv("ASCEND_RT_VISIBLE_DEVICES", raising=False)
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)

    snapshots = take_snapshots(npu_processes=False)
    assert [device.index for device in snapshots.devices] == [0, 1]
    assert snapshots.npu_processes == []

    selected = select_devices(min_count=1)
    assert selected

    collector = ResourceMetricCollector(root_pids=set())
    collector_snapshots = collector.take_snapshots()
    assert [device.index for device in collector_snapshots.devices] == [0, 1]
    assert collector_snapshots.npu_processes == []


def test_python_module_once_only_visible_with_fake_npusmi(tmp_path):
    fake_npusmi = tmp_path / "npu-smi"
    fake_npusmi.write_text(
        "#!/bin/sh\ncat <<'EOF'\n" + RAW_NPUSMI + "\nEOF\n",
        encoding="utf-8",
    )
    fake_npusmi.chmod(fake_npusmi.stat().st_mode | stat.S_IXUSR)

    repo = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["ASCEND_RT_VISIBLE_DEVICES"] = "0"
    env.pop("CUDA_VISIBLE_DEVICES", None)
    env["PATH"] = f"{tmp_path}{os.pathsep}{env.get('PATH', '')}"
    env["PYTHONPATH"] = f"{repo}{os.pathsep}{env.get('PYTHONPATH', '')}"

    result = subprocess.run(
        [sys.executable, "-m", "nputop", "--once", "--only-visible", "--ascii"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "910B2C" in result.stdout


def test_setup_uses_pyproject_package_discovery():
    repo = Path(__file__).resolve().parents[1]
    setup_py = (repo / "setup.py").read_text(encoding="utf-8")
    pyproject = (repo / "pyproject.toml").read_text(encoding="utf-8")

    assert "nvidia-ml-py" not in setup_py
    assert "packages=[" not in setup_py
    assert 'nvisel = "nputop.select:main"' in pyproject
