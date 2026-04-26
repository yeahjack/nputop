import time
from types import SimpleNamespace

from nputop.api import libascend
from nputop.api.utils import NA, Snapshot
from nputop.gui import snapshot as snapshot_module
from nputop.gui.screens.main.process import ProcessPanel
from nputop.gui.snapshot import SnapshotService


class FakeProcess:
    def __init__(self, pid, device):
        self.pid = pid
        self.device = device
        self._ident = (pid, pid, device.index)


class FakeDevice:
    def __init__(self, index, processes=()):
        self.index = index
        self.physical_index = index
        self.tuple_index = (index,)
        self.display_index = str(index)
        self._processes = {process.pid: process for process in processes}
        self.snapshot_calls = 0
        self.process_calls = 0
        self.fail_snapshot = False

    def mig_devices(self):
        return []

    def as_snapshot(self):
        self.snapshot_calls += 1
        if self.fail_snapshot:
            raise RuntimeError("device sample failed")
        snapshot = Snapshot(
            real=self,
            index=self.index,
            physical_index=self.physical_index,
            tuple_index=self.tuple_index,
            display_index=self.display_index,
            memory_used=10,
            memory_total=100,
            npu_utilization=5,
        )
        self._snapshot = snapshot
        return snapshot

    @property
    def snapshot(self):
        return self._snapshot

    def processes(self):
        self.process_calls += 1
        return dict(self._processes)


def cache_stats(*, error=NA, duration=0.1, updated_at=None):
    if updated_at is None:
        updated_at = time.time()
    return libascend.CacheStats(
        cache_ttl=0.5,
        npusmi_timeout=3.0,
        last_update_wall_ts=updated_at,
        last_update_duration=duration,
        last_update_error=error,
        cache_size=1,
    )


def install_fake_host(monkeypatch):
    monkeypatch.setattr(snapshot_module.host, "cpu_percent", lambda: 12.5)
    monkeypatch.setattr(
        snapshot_module.host,
        "virtual_memory",
        lambda: SimpleNamespace(percent=25.0, used=250, total=1000),
    )
    monkeypatch.setattr(
        snapshot_module.host,
        "swap_memory",
        lambda: SimpleNamespace(percent=1.0, used=10, total=1000),
    )
    monkeypatch.setattr(snapshot_module.host, "load_average", lambda: (1.0, 2.0, 3.0))


def install_fake_process_snapshots(monkeypatch):
    def take_snapshots(processes, *, failsafe):
        assert failsafe is True
        return [
            Snapshot(
                real=process,
                pid=process.pid,
                _ident=process._ident,
                device=process.device,
                cpu_percent=1.0,
                memory_percent=2.0,
                host_memory=3,
                npu_memory=4,
                npu_sm_utilization=NA,
                cpu_percent_string="1.0%",
                memory_percent_string="2.0%",
                running_time_human="1s",
                command="python",
            )
            for process in processes
        ]

    monkeypatch.setattr(snapshot_module.NpuProcess, "take_snapshots", take_snapshots)


def test_snapshot_service_collects_device_process_and_host_once(monkeypatch):
    install_fake_host(monkeypatch)
    install_fake_process_snapshots(monkeypatch)
    monkeypatch.setattr(snapshot_module.libascend, "ascendGetCacheStats", lambda: cache_stats())

    device = FakeDevice(0)
    process = FakeProcess(123, device)
    device._processes = {process.pid: process}
    service = SnapshotService([device], interval=10.0)

    bundle = service.collect()

    assert bundle.generation == 1
    assert [snapshot.index for snapshot in bundle.devices] == [0]
    assert [snapshot.pid for snapshot in bundle.processes] == [123]
    assert bundle.host.cpu_percent == 12.5
    assert bundle.status.state == "ok"
    assert device.snapshot_calls == 1
    assert device.process_calls == 1


def test_snapshot_service_keeps_previous_frame_on_failure(monkeypatch):
    install_fake_host(monkeypatch)
    install_fake_process_snapshots(monkeypatch)
    monkeypatch.setattr(snapshot_module.libascend, "ascendGetCacheStats", lambda: cache_stats())

    device = FakeDevice(0)
    service = SnapshotService([device], interval=10.0)
    first = service.collect()

    device.fail_snapshot = True
    failed = service.collect()

    assert failed.generation == first.generation + 1
    assert failed.devices == first.devices
    assert failed.processes == first.processes
    assert failed.status.state == "error"
    assert "RuntimeError" in failed.status.last_error


def test_snapshot_service_manual_refresh_invalidates_backend(monkeypatch):
    install_fake_host(monkeypatch)
    install_fake_process_snapshots(monkeypatch)
    monkeypatch.setattr(snapshot_module.libascend, "ascendGetCacheStats", lambda: cache_stats())
    invalidations = []
    monkeypatch.setattr(
        snapshot_module.libascend,
        "ascendInvalidateCache",
        lambda: invalidations.append(True),
    )

    service = SnapshotService([FakeDevice(0)], interval=10.0)
    service.request_refresh(block=True)

    assert invalidations == [True]
    assert service.snapshot().generation == 1


def test_snapshot_service_reports_backend_status(monkeypatch):
    install_fake_host(monkeypatch)
    install_fake_process_snapshots(monkeypatch)
    monkeypatch.setattr(
        snapshot_module.libascend,
        "ascendGetCacheStats",
        lambda: cache_stats(error="TimeoutExpired: slow backend"),
    )

    service = SnapshotService([FakeDevice(0)], interval=10.0)
    bundle = service.collect()

    assert bundle.status.state == "error"
    assert "TimeoutExpired" in service.status_text()


def test_snapshot_service_reports_slow_and_stale_samples(monkeypatch):
    install_fake_host(monkeypatch)
    install_fake_process_snapshots(monkeypatch)
    monkeypatch.setattr(
        snapshot_module.libascend,
        "ascendGetCacheStats",
        lambda: cache_stats(duration=2.0),
    )

    service = SnapshotService([FakeDevice(0)], interval=10.0)
    assert service.collect().status.state == "slow"

    monkeypatch.setattr(
        snapshot_module.libascend,
        "ascendGetCacheStats",
        lambda: cache_stats(updated_at=time.time() - 10.0),
    )
    service = SnapshotService([FakeDevice(0)], interval=0.5)
    assert service.collect().status.state == "stale"


def test_process_panel_reads_process_snapshots_from_service():
    class FakeService:
        def __init__(self, snapshots):
            self.snapshots = snapshots
            self.ensure_values = []

        def process_snapshots(self, *, ensure=False):
            self.ensure_values.append(ensure)
            return list(self.snapshots)

    class DeviceThatMustNotSample:
        index = 0
        physical_index = 0
        tuple_index = (0,)
        display_index = "0"

        def processes(self):
            raise AssertionError("ProcessPanel should read the shared SnapshotService")

    device = DeviceThatMustNotSample()
    process = Snapshot(
        real=SimpleNamespace(_ident=(1, 1, 0)),
        pid=1,
        _ident=(1, 1, 0),
        device=device,
        cpu_percent_string="1.0%",
        memory_percent_string="2.0%",
        running_time_human="1s",
        command="python",
    )
    service = FakeService([process])
    panel = object.__new__(ProcessPanel)
    panel.root = SimpleNamespace(snapshot_service=service)
    panel.filters = [None]

    snapshots = panel.take_snapshots()

    assert service.ensure_values == [True]
    assert snapshots == [process]
    assert str(process.host_info).endswith("python")
