# This file is part of nputop, the interactive Ascend-NPU process viewer.
#
# Copyright (c) 2025 Xuehai Pan <XuehaiPan@pku.edu.cn>
# Copyright (c) 2025 Lianzhong You <youlianzhong@gml.ac.cn>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations
import os
import subprocess, re, time, sys, threading
from collections import namedtuple
from pathlib import Path
from types import ModuleType
from typing import Any
from collections.abc import Callable
import platform

# --------- 常量 ----------
NA            : str  = "N/A"
UINT_MAX      : int  = 0xFFFFFFFF
ULONGLONG_MAX : int  = 0xFFFFFFFFFFFFFFFF


class NVMLError(Exception):
    """Base exception for NVML-compatible Ascend errors."""


class NVMLError_LibraryNotFound(NVMLError):
    """Raised when the Ascend management backend cannot be loaded."""


class NVMLError_DriverNotLoaded(NVMLError):
    """Raised when the Ascend driver is not ready."""


class NVMLError_NotFound(NVMLError):
    """Raised when a requested device cannot be found."""


class NVMLError_InvalidArgument(NVMLError):
    """Raised when a requested device identifier is invalid."""


# --------- 全局缓存 ----------
_CACHE      : dict[int, dict[str,Any]] = {}   # 物理 id ↦ 数据
_IDX        : list[int] = []                  # 逻辑 index ↦ 物理 id
_CACHE_TTL  = 0.5
_CACHE_TTL_ENV = "NPUTOP_NPUSMI_TTL"
_NPUSMI_TIMEOUT = 3.0
_NPUSMI_TIMEOUT_ENV = "NPUTOP_NPUSMI_TIMEOUT"
_cache_ts   = 0.0
_last_update_wall_ts = 0.0
_last_update_duration: float | str = NA
_last_update_error: str = NA
_CACHE_LOCK = threading.RLock()
_CACHE_REFRESH_LOCK = threading.Lock()
_DRIVER_VERSION = None
_CANN_VERSION = None
_POWER_LIMIT = {
    "310": None,
    "310B": None,
    "310P1": 90,
    "310P3": 72,
    "910A": 310,
    "910B": 265,
    "910B1": 430,
    "910B2": 420,
    "910B3": 350,
    "910C": 350,
}
_npu_chip_phy : dict[tuple[int, int], int] = {} # (npu id, chip_id) ↦ phy id
# --------- Regex ----------
_RE_L1 = re.compile(r"^\|\s*(\d+)\s+(\S+).*?\|\s*(\S+)\s+\|\s*(\S+)\s+(\d+)")
_RE_L2 = re.compile(r"^\|\s*(\d+)\s+(\d*)\s*\|\s*([0-9A-Fa-f:.]+|NA)\s*\|\s*(\d+).*?\|$")
_RE_P  = re.compile(r"^\|\s*(\d+)\s+(\d+)\s+\|\s+(\d+)\s+\|.*?\|\s+(\d+)")
_RE_R = re.compile(r"^\|\s*(\S+)\s+([\d.rcRC]+)\s+Version:\s*([\d.rcRC]+)")

Util = namedtuple("UtilizationRates", ["npu", "mem", "bandwidth", "aicpu"])
CacheStats = namedtuple(
    "CacheStats",
    [
        "cache_ttl",
        "npusmi_timeout",
        "last_update_wall_ts",
        "last_update_duration",
        "last_update_error",
        "cache_size",
    ],
)

def _float_from_env(name: str, default: float, min_value: float = 0.0) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= min_value else default

def _cache_ttl() -> float:
    return _float_from_env(_CACHE_TTL_ENV, _CACHE_TTL)

def _npusmi_timeout() -> float:
    return _float_from_env(_NPUSMI_TIMEOUT_ENV, _NPUSMI_TIMEOUT, min_value=0.1)

def _cache_is_fresh(now: float | None = None) -> bool:
    if now is None:
        now = time.monotonic()
    return now - _cache_ts < _cache_ttl()

def _has_cache() -> bool:
    with _CACHE_LOCK:
        return bool(_IDX)

def _parse_npusmi(raw: str) -> tuple[dict[int, dict[str, Any]], dict[tuple[int, int], int], str | None]:
    lines = raw.splitlines()
    data: dict[int, dict[str,Any]] = {}
    chip_phy: dict[tuple[int, int], int] = {}
    driver_version = None

    for line in lines:
        m0 = _RE_R.match(line.strip())
        if m0:
            _, driver_version, _ = m0.groups()
            break

    i = 0
    while i < len(lines):
        ln = lines[i].strip()

        m1 = _RE_L1.match(ln)
        if m1:
            npu_id, name, ok, pwr, tmp = m1.groups()
            cur_id = int(npu_id)

            ln1_data = dict(
                name=name, health=ok,
                power=float(pwr) * 1000 if (pwr != '-' and pwr != "NA") else NA + " ",
                temp=int(tmp),
                procs=[],
                npu_id=cur_id,
                chip_id=0,
            )

            i += 1
            ln_l2 = lines[i].strip() if i < len(lines) else ""
            m2 = _RE_L2.match(ln_l2)

            if m2:
                chip_id, phy_id, bus, aic = m2.groups()
                chip_id_kwargs = {'chip_id': int(chip_id)} if chip_id else {}

                if phy_id:
                    cur_id = int(phy_id)
                d = data.setdefault(cur_id, {})
                d.update(ln1_data)

                d.update(
                    bus_id=bus,
                    aicore=int(aic),
                    **chip_id_kwargs,
                )
                pairs = re.findall(r'(\d+)\s*/\s*(\d+)', ln_l2)
                if pairs:
                    h_used, h_tot = map(int, pairs[-1])
                    d.update(
                        hbm_used=h_used * 1024 * 1024,
                        hbm_total=h_tot * 1024 * 1024,
                    )
                chip_phy[(d['npu_id'], d['chip_id'])] = cur_id
            else:
                d = data.setdefault(cur_id, {})
                d.update(ln1_data)
                chip_phy[(d['npu_id'], d['chip_id'])] = cur_id

            i += 1
            continue

        mp = _RE_P.match(ln)
        if mp:
            npu_id, chip_id, pid, mem = map(int, mp.groups())
            phy_id = chip_phy.get((npu_id, chip_id))
            if phy_id is not None:
                d = data.setdefault(phy_id, {})
                d.setdefault("procs", []).append((pid, mem * 1024 * 1024))

        i += 1

    for d in data.values():
        d.setdefault("power", NA); d.setdefault("temp", NA)
        d.setdefault("aicore", NA)
        d.setdefault("hbm_used", 0); d.setdefault("hbm_total", 0)
        d.setdefault("procs", [])
        mem_pct = (round(100*d["hbm_used"]/d["hbm_total"],1)
                   if d["hbm_total"] else NA)
        d["util"] = Util(d["aicore"], mem_pct, NA, NA)

    return data, chip_phy, driver_version

def _install_cache(
    data: dict[int, dict[str, Any]],
    chip_phy: dict[tuple[int, int], int],
    driver_version: str | None,
    duration: float,
) -> None:
    global _cache_ts
    global _last_update_wall_ts
    global _last_update_duration
    global _last_update_error
    global _DRIVER_VERSION

    with _CACHE_LOCK:
        _CACHE.clear(); _CACHE.update(data)
        _IDX.clear();   _IDX.extend(sorted(_CACHE.keys()))
        _npu_chip_phy.clear(); _npu_chip_phy.update(chip_phy)
        if driver_version is not None:
            _DRIVER_VERSION = driver_version
        _cache_ts = time.monotonic()
        _last_update_wall_ts = time.time()
        _last_update_duration = duration
        _last_update_error = NA

def _record_cache_failure(exc: BaseException, duration: float) -> None:
    global _cache_ts
    global _last_update_wall_ts
    global _last_update_duration
    global _last_update_error

    with _CACHE_LOCK:
        _cache_ts = time.monotonic()
        _last_update_wall_ts = time.time()
        _last_update_duration = duration
        _last_update_error = f"{exc.__class__.__name__}: {exc}"

def _update_cache(raw: str = None) -> None:
    if raw is not None:
        start = time.monotonic()
        data, chip_phy, driver_version = _parse_npusmi(raw)
        _install_cache(data, chip_phy, driver_version, time.monotonic() - start)
        return

    if _cache_is_fresh():
        return

    # Existing cache users should not queue behind a slow npu-smi refresh.
    # The first caller without any cache still blocks so startup can discover devices.
    acquired = _CACHE_REFRESH_LOCK.acquire(blocking=not _has_cache())
    if not acquired:
        return

    try:
        if _cache_is_fresh():
            return

        start = time.monotonic()
        try:
            result = subprocess.run(
                ["npu-smi","info"],
                text=True,
                capture_output=True,
                timeout=_npusmi_timeout(),
            )
            if not result.stdout.strip():
                raise RuntimeError(result.stderr.strip() or "npu-smi info returned empty output")
            data, chip_phy, driver_version = _parse_npusmi(result.stdout)
            _install_cache(data, chip_phy, driver_version, time.monotonic() - start)
        except Exception as exc:
            _record_cache_failure(exc, time.monotonic() - start)
    finally:
        _CACHE_REFRESH_LOCK.release()

def _phys(idx: int) -> int|None:
    _update_cache()
    with _CACHE_LOCK:
        if 0 <= idx < len(_IDX):
            return _IDX[idx]
    return None

def _device_data(idx: int) -> dict[str, Any]:
    id = _phys(idx)
    if id is None:
        return {}
    with _CACHE_LOCK:
        return dict(_CACHE.get(id, {}))

MemInfo  = namedtuple("MemoryInfo","total free used")
ProcInfo = namedtuple("Proc","pid usedNpuMemory")

def ascendDeviceGetCount() -> int:
    _update_cache()
    with _CACHE_LOCK:
        return len(_IDX)

def ascendDeviceGetName(i:int):             return _device_data(i).get("name",NA)
def ascendDeviceGetTemperature(i:int):      return _device_data(i).get("temp",NA)
def ascendDeviceGetPowerUsage(i:int):       return _device_data(i).get("power",NA)
def ascendDeviceGetUtilizationRates(i:int): return _device_data(i).get("util",NA)

def ascendDeviceGetMemoryInfo(i:int):
    d=_device_data(i)
    if not d: return MemInfo(0,0,0)
    tot=d.get("hbm_total",0); used=d.get("hbm_used",0)
    return MemInfo(tot, tot-used, used)

def ascendDeviceGetProcessInfo(i:int):
    return [ProcInfo(pid,mem) for pid,mem in _device_data(i).get("procs",[])]

def ascendSystemGetDriverVersion() -> str:
    global _DRIVER_VERSION
    return _DRIVER_VERSION or NA

def ascendSystemGetCANNVersion() -> str:
    global _CANN_VERSION
    if _CANN_VERSION is not None:
        return _CANN_VERSION

    arch = platform.machine()
    arch_subdir_map = {"x86_64": "x86_64-linux", "aarch64": "aarch64-linux"}
    arch_subdir = arch_subdir_map.get(arch)
    if arch_subdir is None:
        _CANN_VERSION = NA
        return _CANN_VERSION

    path = Path(f'/usr/local/Ascend/ascend-toolkit/latest/{arch_subdir}/ascend_toolkit_install.info')
    try:
        output = path.read_text(encoding="utf-8", errors="replace")
        
        match = re.search(r'version\s*=\s*([\w.+-]+)', output)
        _CANN_VERSION = match.group(1) if match else NA
        return _CANN_VERSION
            
    except OSError:
        _CANN_VERSION = NA
        return _CANN_VERSION
    
def ascendDeviceGetPowerLimit(i:int):
    chip_name = _device_data(i).get("name")
    return _POWER_LIMIT.get(chip_name, NA)

def ascendGetCacheStats() -> CacheStats:
    with _CACHE_LOCK:
        return CacheStats(
            cache_ttl=_cache_ttl(),
            npusmi_timeout=_npusmi_timeout(),
            last_update_wall_ts=_last_update_wall_ts,
            last_update_duration=_last_update_duration,
            last_update_error=_last_update_error,
            cache_size=len(_IDX),
        )

def ascendInvalidateCache() -> None:
    global _cache_ts
    with _CACHE_LOCK:
        _cache_ts = 0.0

def nvmlCheckReturn(v:Any, t:type|tuple[type,...]|None=None)->bool:
    return v != NA and (isinstance(v,t) if t else True)

def nvmlQuery(func:Callable|str,*a,default:Any=NA,**kw)->Any:
    try:
        f = globals()[func] if isinstance(func,str) else func
        return f(*a,**kw)
    except Exception:
        return default

VERSIONED_PATTERN = re.compile(r"^(?P<name>\w+)(?P<suffix>_v\d+)$")

class _Mod(ModuleType):
    def __getattr__(self,n): return globals()[n]
    def __enter__(self): return self
    def __exit__(self,*exc): ...
sys.modules[__name__].__class__ = _Mod
