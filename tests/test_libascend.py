import pytest

from nputop.api import libascend


# (raw_output, expected_cache)
TEST_CASES = [
    (
        # npusmi_hbm
        """
+------------------------------------------------------------------------------------------------+ 
| npu-smi 23.0.2.1                 Version: 23.0.2.1                                             | 
+---------------------------+---------------+----------------------------------------------------+ 
| NPU   Name                | Health        | Power(W)    Temp(C)           Hugepages-Usage(page)| 
| Chip                      | Bus-Id        | AICore(%)   Memory-Usage(MB)  HBM-Usage(MB)        | 
+===========================+===============+====================================================+ 
| 0     910B2C              | OK            | 88.6        51                0    / 0             | 
| 0                         | 0000:5A:00.0  | 0           0    / 0          20701/ 65536         | 
+===========================+===============+====================================================+ 
| 1     910B2C              | OK            | 99.6        50                0    / 0             | 
| 0                         | 0000:19:00.0  | 0           0    / 0          20687/ 65536         | 
+===========================+===============+====================================================+ 
+---------------------------+---------------+----------------------------------------------------+ 
| NPU     Chip              | Process id    | Process name             | Process memory(MB)      | 
+===========================+===============+====================================================+ 
| 0       0                 | 124528        | python3.8                | 17400                   | 
+---------------------------+---------------+----------------------------------------------------+ 
""",
        {
            0: {
                'name': '910B2C',
                'health': 'OK',
                'power': 88600.0,
                'temp': 51,
                'procs': [(124528, 18245222400)],
                'bus_id': '0000:5A:00.0',
                'aicore': 0,
                'hbm_used': 21706571776,
                'hbm_total': 68719476736,
                'util': libascend.Util(npu=0, mem=31.6, bandwidth='N/A', aicpu='N/A'),
                'npu_id': 0,
                'chip_id': 0,
            },
            1: {
                'name': '910B2C',
                'health': 'OK',
                'power': 99600.0,
                'temp': 50,
                'procs': [],
                'bus_id': '0000:19:00.0',
                'aicore': 0,
                'hbm_used': 21691891712,
                'hbm_total': 68719476736,
                'util': libascend.Util(npu=0, mem=31.6, bandwidth='N/A', aicpu='N/A'),
                'npu_id': 1,
                'chip_id': 0,
            },
        },
    ),
    (
        # npusmi_nohbm
        """
+--------------------------------------------------------------------------------------------------------+ 
| npu-smi 23.0.0                                   Version: 23.0.0                                       | 
+-------------------------------+-----------------+------------------------------------------------------+ 
| NPU     Name                  | Health          | Power(W)     Temp(C)           Hugepages-Usage(page) | 
| Chip    Device                | Bus-Id          | AICore(%)    Memory-Usage(MB)                        | 
+===============================+=================+======================================================+ 
| 0       310B4                 | Alarm           | 0.0          65                15    / 15            | 
| 0       0                     | NA              | 0            3628 / 15609                            | 
+===============================+=================+======================================================+ 
""",
        {
            0: {
                'name': '310B4',
                'health': 'Alarm',
                'power': 0.0,
                'temp': 65,
                'procs': [],
                'bus_id': 'NA',
                'aicore': 0,
                'hbm_used': 3804233728,
                'hbm_total': 16367222784,
                'util': libascend.Util(npu=0, mem=23.2, bandwidth='N/A', aicpu='N/A'),
                'npu_id': 0,
                'chip_id': 0,
            }
        },
    ),
    (
        # npusmi_empty
        """
+------------------------------------------------------------------------------------------------+
| npu-smi 25.2.0                   Version: 25.2.0                                               |
+---------------------------+---------------+----------------------------------------------------+
| NPU   Name                | Health        | Power(W)    Temp(C)           Hugepages-Usage(page)|
| Chip  Phy-ID              | Bus-Id        | AICore(%)   Memory-Usage(MB)  HBM-Usage(MB)        |
+===========================+===============+====================================================+
| 0     Ascend910           | OK            | 162.8       37                0    / 0             |
| 0     0                   | 0000:9C:00.0  | 0           0    / 0          3133 / 65536         |
+------------------------------------------------------------------------------------------------+
| 0     Ascend910           | OK            | -           37                0    / 0             |
| 1     1                   | 0000:9E:00.0  | 0           0    / 0          2876 / 65536         |
+===========================+===============+====================================================+
| 1     Ascend910           | OK            | 167.1       38                0    / 0             |
| 0     2                   | 0000:37:00.0  | 0           0    / 0          3116 / 65536         |
+------------------------------------------------------------------------------------------------+
| 1     Ascend910           | OK            | -           38                0    / 0             |
| 1     3                   | 0000:39:00.0  | 0           0    / 0          10568/ 65536         |
+===========================+===============+====================================================+
+---------------------------+---------------+----------------------------------------------------+
| NPU     Chip              | Process id    | Process name             | Process memory(MB)      |
+===========================+===============+====================================================+
| No running processes found in NPU 0                                                            |
+===========================+===============+====================================================+
| 1       1                 | 990711        | python                   | 7746                    |
+===========================+===============+====================================================+
""",
        {
            0: {
                'name': 'Ascend910',
                'health': 'OK',
                'power': 162800.0,
                'temp': 37,
                'procs': [],
                'bus_id': '0000:9C:00.0',
                'aicore': 0,
                'hbm_used': 3133 * 1024 * 1024,
                'hbm_total': 65536 * 1024 * 1024,
                'util': libascend.Util(npu=0, mem=4.8, bandwidth='N/A', aicpu='N/A'),
                'npu_id': 0,
                'chip_id': 0,
            },
            1: {
                'name': 'Ascend910',
                'health': 'OK',
                'power': libascend.NA + ' ',
                'temp': 37,
                'procs': [],
                'bus_id': '0000:9E:00.0',
                'aicore': 0,
                'hbm_used': 2876 * 1024 * 1024,
                'hbm_total': 65536 * 1024 * 1024,
                'util': libascend.Util(npu=0, mem=4.4, bandwidth='N/A', aicpu='N/A'),
                'npu_id': 0,
                'chip_id': 1,
            },
            2: {
                'name': 'Ascend910',
                'health': 'OK',
                'power': 167100.0,
                'temp': 38,
                'procs': [],
                'bus_id': '0000:37:00.0',
                'aicore': 0,
                'hbm_used': 3116 * 1024 * 1024,
                'hbm_total': 65536 * 1024 * 1024,
                'util': libascend.Util(npu=0, mem=4.8, bandwidth='N/A', aicpu='N/A'),
                'npu_id': 1,
                'chip_id': 0,
            },
            3: {
                'name': 'Ascend910',
                'health': 'OK',
                'power': libascend.NA + ' ',
                'temp': 38,
                'procs': [(990711, 7746 * 1024 * 1024)],
                'bus_id': '0000:39:00.0',
                'aicore': 0,
                'hbm_used': 10568 * 1024 * 1024,
                'hbm_total': 65536 * 1024 * 1024,
                'util': libascend.Util(npu=0, mem=16.1, bandwidth='N/A', aicpu='N/A'),
                'npu_id': 1,
                'chip_id': 1,
            },
        },
    ),
    (
        """
+--------------------------------------------------------------------------------------------------------+
| npu-smi 24.1.0.1                                 Version: 24.1.0.1                                     |
+-------------------------------+-----------------+------------------------------------------------------+
| NPU     Name                  | Health          | Power(W)     Temp(C)           Hugepages-Usage(page) |
| Chip    Device                | Bus-Id          | AICore(%)    Memory-Usage(MB)                        |
+===============================+=================+======================================================+
| 1       310P3                 | OK              | NA           62                7210  / 7210          |
| 0       0                     | 0000:01:00.0    | 0            16302/ 44280                            |
+-------------------------------+-----------------+------------------------------------------------------+
| 1       310P3                 | OK              | NA           62                7210  / 7210          |
| 1       1                     | 0000:01:00.0    | 0            15543/ 43693                            |
+===============================+=================+======================================================+
| 2       310P3                 | OK              | NA           61                17057 / 17057         |
| 0       2                     | 0000:02:00.0    | 0            35563/ 44280                            |
+-------------------------------+-----------------+------------------------------------------------------+
| 2       310P3                 | OK              | NA           61                16823 / 16823         |
| 1       3                     | 0000:02:00.0    | 0            35204/ 43693                            |
+===============================+=================+======================================================+
+-------------------------------+-----------------+------------------------------------------------------+
| NPU     Chip                  | Process id      | Process name             | Process memory(MB)        |
+===============================+=================+======================================================+
| 1       0                     | 3277562         | mindie_llm_back          | 14513                     |
| 1       1                     | 3277565         | mindie_llm_back          | 14513                     |
+===============================+=================+======================================================+
| 2       0                     | 3034986         | mindie_llm_back          | 34207                     |
| 2       1                     | 3034989         | mindie_llm_back          | 33740                     |
+===============================+=================+======================================================+
""",
        {
            0: {
                'name': '310P3',
                'health': 'OK',
                'power': libascend.NA + ' ',
                'temp': 62,
                'procs': [(3277562, 14513*1024*1024)],
                'bus_id': '0000:01:00.0',
                'aicore': 0,
                'hbm_used': 16302 * 1024 * 1024,
                'hbm_total': 44280 * 1024 * 1024,
                'util': libascend.Util(npu=0, mem=36.8, bandwidth='N/A', aicpu='N/A'),
                'npu_id': 1,
                'chip_id': 0,
            },
            1: {
                'name': '310P3',
                'health': 'OK',
                'power': libascend.NA + ' ',
                'temp': 62,
                'procs': [(3277565, 14513*1024*1024)],
                'bus_id': '0000:01:00.0',
                'aicore': 0,
                'hbm_used': 15543 * 1024 * 1024,
                'hbm_total': 43693 * 1024 * 1024,
                'util': libascend.Util(npu=0, mem=35.6, bandwidth='N/A', aicpu='N/A'),
                'npu_id': 1,
                'chip_id': 1,
            },
            2: {
                'name': '310P3',
                'health': 'OK',
                'power': libascend.NA + ' ',
                'temp': 61,
                'procs': [(3034986, 34207*1024*1024)],
                'bus_id': '0000:02:00.0',
                'aicore': 0,
                'hbm_used': 35563 * 1024 * 1024,
                'hbm_total': 44280 * 1024 * 1024,
                'util': libascend.Util(npu=0, mem=80.3, bandwidth='N/A', aicpu='N/A'),
                'npu_id': 2,
                'chip_id': 0,
            },
            3: {
                'name': '310P3',
                'health': 'OK',
                'power': libascend.NA + ' ',
                'temp': 61,
                'procs': [(3034989, 33740*1024*1024)],
                'bus_id': '0000:02:00.0',
                'aicore': 0,
                'hbm_used': 35204 * 1024 * 1024,
                'hbm_total': 43693 * 1024 * 1024,
                'util': libascend.Util(npu=0, mem=80.6, bandwidth='N/A', aicpu='N/A'),
                'npu_id': 2,
                'chip_id': 1,
            },
        },
    ),

]


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


@pytest.mark.parametrize("raw,expected_cache", TEST_CASES)
def test_npusmi_parse(raw, expected_cache):
    reset_libascend_cache()

    libascend._update_cache(raw)

    assert list(expected_cache.keys()) == libascend._IDX

    for key, expected_val in expected_cache.items():
        assert key in libascend._CACHE
        cached_val = libascend._CACHE[key]
        for field, value in expected_val.items():
            assert cached_val[field] == value


def test_npusmi_parse_ignores_transient_unknown_process():
    raw = TEST_CASES[0][0].replace(
        "| 0       0                 | 124528",
        "| 7       0                 | 124528",
    )
    reset_libascend_cache()

    libascend._update_cache(raw)

    assert libascend._IDX == [0, 1]
    assert libascend._CACHE[0]["procs"] == []
    assert libascend._CACHE[1]["procs"] == []


def test_npusmi_cache_ttl_is_env_configurable(monkeypatch):
    monkeypatch.setenv("NPUTOP_NPUSMI_TTL", "0.25")
    assert libascend._cache_ttl() == 0.25

    monkeypatch.setenv("NPUTOP_NPUSMI_TTL", "bad")
    assert libascend._cache_ttl() == libascend._CACHE_TTL


def test_npusmi_cache_timeout_is_env_configurable(monkeypatch):
    monkeypatch.setenv("NPUTOP_NPUSMI_TIMEOUT", "1.25")
    assert libascend._npusmi_timeout() == 1.25

    monkeypatch.setenv("NPUTOP_NPUSMI_TIMEOUT", "0")
    assert libascend._npusmi_timeout() == libascend._NPUSMI_TIMEOUT


def test_npusmi_cache_returns_stale_frame_when_refresh_is_busy(monkeypatch):
    reset_libascend_cache()
    libascend._update_cache(TEST_CASES[0][0])
    libascend._cache_ts = 0.0

    def fail_run(*args, **kwargs):
        raise AssertionError("stale cache path should not spawn npu-smi")

    monkeypatch.setattr(libascend.subprocess, "run", fail_run)
    assert libascend._CACHE_REFRESH_LOCK.acquire(blocking=False)
    try:
        assert libascend.ascendDeviceGetCount() == 2
        assert libascend.ascendDeviceGetName(0) == "910B2C"
    finally:
        libascend._CACHE_REFRESH_LOCK.release()


def test_npusmi_cache_keeps_stale_frame_on_failed_refresh(monkeypatch):
    reset_libascend_cache()
    libascend._update_cache(TEST_CASES[0][0])
    libascend._cache_ts = 0.0

    def fail_run(*args, **kwargs):
        raise libascend.subprocess.TimeoutExpired(cmd="npu-smi info", timeout=1)

    monkeypatch.setattr(libascend.subprocess, "run", fail_run)

    assert libascend.ascendDeviceGetCount() == 2
    assert libascend.ascendDeviceGetMemoryInfo(0).used == 21706571776
    stats = libascend.ascendGetCacheStats()
    assert stats.cache_size == 2
    assert "TimeoutExpired" in stats.last_update_error


def test_npusmi_cache_records_success_stats():
    reset_libascend_cache()

    libascend._update_cache(TEST_CASES[0][0])

    stats = libascend.ascendGetCacheStats()
    assert stats.cache_size == 2
    assert stats.last_update_wall_ts > 0
    assert stats.last_update_duration >= 0
    assert stats.last_update_error == libascend.NA
