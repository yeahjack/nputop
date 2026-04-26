# This file is part of nputop, the interactive Ascend-NPU process viewer.
# License: GNU GPL version 3.


"""Shared GUI snapshot sampling service."""

from __future__ import annotations

import itertools
import threading
import time
from typing import Any, NamedTuple

from nputop.api import libascend
from nputop.gui.library import NA, NpuProcess, Snapshot, host


class HostSnapshot(NamedTuple):
    cpu_percent: Any = NA
    virtual_memory: Any = None
    swap_memory: Any = None
    load_average: Any = None


class SamplingStatus(NamedTuple):
    state: str
    message: str
    timestamp: float
    duration: float | str
    backend_duration: float | str
    last_error: str
    stale: bool
    slow: bool


class SnapshotBundle(NamedTuple):
    generation: int
    timestamp: float
    devices: list[Snapshot]
    processes: list[Snapshot]
    host: HostSnapshot
    status: SamplingStatus


class SnapshotService:
    """Collect device, process and host snapshots through one GUI sampler."""

    DEFAULT_INTERVAL = 0.5
    DEFAULT_SLOW_THRESHOLD = 1.0

    def __init__(self, devices, interval: float | None = None) -> None:
        self.devices = list(devices)
        self.all_devices = []
        self.leaf_devices = []
        for device in self.devices:
            self.all_devices.append(device)
            mig_devices = device.mig_devices()
            if len(mig_devices) > 0:
                self.all_devices.extend(mig_devices)
                self.leaf_devices.extend(mig_devices)
            else:
                self.leaf_devices.append(device)

        self._lock = threading.RLock()
        self._collect_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._refresh_event = threading.Event()
        self._force_refresh = False
        self._thread = threading.Thread(
            name='gui-snapshot-service',
            target=self._target,
            daemon=True,
        )
        self._started = False
        self._generation = 0
        self.interval = self.DEFAULT_INTERVAL
        if interval is not None:
            self.set_interval(interval)
        self.slow_threshold = self.DEFAULT_SLOW_THRESHOLD
        self._bundle = SnapshotBundle(
            generation=0,
            timestamp=0.0,
            devices=[],
            processes=[],
            host=HostSnapshot(),
            status=self._make_status(timestamp=0.0, duration=NA),
        )

    def set_interval(self, interval: float) -> None:
        assert interval > 0.0
        self.interval = float(interval)
        self._refresh_event.set()

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread.start()
        self._refresh_event.set()

    def stop(self) -> None:
        self._stop_event.set()
        self._refresh_event.set()
        if self._started and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def request_refresh(self, *, block: bool = False) -> SnapshotBundle:
        with self._lock:
            self._force_refresh = True
        if block:
            return self.collect()
        self._refresh_event.set()
        return self.snapshot()

    def snapshot(self, *, ensure: bool = False) -> SnapshotBundle:
        with self._lock:
            has_snapshot = self._bundle.generation > 0
        if ensure and not has_snapshot:
            return self.collect()
        with self._lock:
            return self._bundle

    def device_snapshots(self, *, ensure: bool = False) -> list[Snapshot]:
        return list(self.snapshot(ensure=ensure).devices)

    def process_snapshots(self, *, ensure: bool = False) -> list[Snapshot]:
        return list(self.snapshot(ensure=ensure).processes)

    def host_snapshot(self, *, ensure: bool = False) -> HostSnapshot:
        return self.snapshot(ensure=ensure).host

    def process_snapshot_for(self, process, *, ensure: bool = False) -> Snapshot | None:
        real_process = process.real if isinstance(process, Snapshot) else process
        try:
            identity = real_process._ident  # pylint: disable=protected-access
        except AttributeError:
            return None

        for snapshot in self.process_snapshots(ensure=ensure):
            try:
                if snapshot._ident == identity:  # pylint: disable=protected-access
                    return snapshot
            except AttributeError:
                continue
        return None

    def status_text(self) -> str:
        status = self.snapshot().status
        if status.state == 'idle':
            return 'Sample: pending'
        if status.state == 'error':
            return f'Sample: ERROR {status.last_error}'
        if status.stale:
            return f'Sample: STALE {status.message}'
        if status.slow:
            return f'Sample: SLOW {status.message}'
        return f'Sample: OK {status.message}'

    def collect(self) -> SnapshotBundle:
        if not self._collect_lock.acquire(blocking=False):
            return self.snapshot(ensure=False)
        try:
            with self._lock:
                force_refresh = self._force_refresh
                self._force_refresh = False
            if force_refresh:
                self._invalidate_backend_cache()

            started = time.monotonic()
            try:
                device_snapshots = [device.as_snapshot() for device in self.all_devices]
                processes = list(
                    itertools.chain.from_iterable(
                        device.processes().values() for device in self.leaf_devices
                    ),
                )
                process_snapshots = NpuProcess.take_snapshots(processes, failsafe=True)
                host_snapshot = HostSnapshot(
                    cpu_percent=host.cpu_percent(),
                    virtual_memory=host.virtual_memory(),
                    swap_memory=host.swap_memory(),
                    load_average=host.load_average(),
                )
            except Exception as exc:  # noqa: BLE001
                return self._publish_failure(exc, time.monotonic() - started)

            return self._publish(
                devices=device_snapshots,
                processes=process_snapshots,
                host_snapshot=host_snapshot,
                duration=time.monotonic() - started,
                error=NA,
            )
        finally:
            self._collect_lock.release()

    def _target(self) -> None:
        next_snapshot = time.monotonic()
        while not self._stop_event.is_set():
            timeout = max(0.0, next_snapshot - time.monotonic())
            self._refresh_event.wait(timeout=timeout)
            self._refresh_event.clear()
            if self._stop_event.is_set():
                break

            self.collect()
            next_snapshot = time.monotonic() + self.interval

    def _publish(
        self,
        *,
        devices: list[Snapshot],
        processes: list[Snapshot],
        host_snapshot: HostSnapshot,
        duration: float,
        error: str,
    ) -> SnapshotBundle:
        timestamp = time.time()
        with self._lock:
            self._generation += 1
            self._bundle = SnapshotBundle(
                generation=self._generation,
                timestamp=timestamp,
                devices=devices,
                processes=processes,
                host=host_snapshot,
                status=self._make_status(
                    timestamp=timestamp,
                    duration=duration,
                    error=error,
                ),
            )
            return self._bundle

    def _publish_failure(self, exc: BaseException, duration: float) -> SnapshotBundle:
        timestamp = time.time()
        error = f'{exc.__class__.__name__}: {exc}'
        with self._lock:
            previous = self._bundle
            self._generation += 1
            self._bundle = SnapshotBundle(
                generation=self._generation,
                timestamp=timestamp,
                devices=previous.devices,
                processes=previous.processes,
                host=previous.host,
                status=self._make_status(
                    timestamp=timestamp,
                    duration=duration,
                    error=error,
                ),
            )
            return self._bundle

    def _make_status(
        self,
        *,
        timestamp: float,
        duration: float | str,
        error: str = NA,
    ) -> SamplingStatus:
        backend_duration: float | str = NA
        backend_error = NA
        stale = False
        try:
            stats = libascend.ascendGetCacheStats()
        except Exception:  # noqa: BLE001
            stats = None

        if stats is not None:
            backend_duration = stats.last_update_duration
            backend_error = stats.last_update_error
            if stats.last_update_wall_ts:
                stale_after = max(2.0, self.interval * 3.0, float(stats.cache_ttl) * 3.0)
                stale = time.time() - stats.last_update_wall_ts > stale_after

        last_error = error if error != NA else backend_error
        slow = any(
            isinstance(value, (int, float)) and value > self.slow_threshold
            for value in (duration, backend_duration)
        )

        if timestamp == 0.0:
            state = 'idle'
            message = 'pending'
        elif last_error != NA:
            state = 'error'
            message = str(last_error)
        elif stale:
            state = 'stale'
            message = self._format_duration(duration)
        elif slow:
            state = 'slow'
            message = self._format_duration(duration)
        else:
            state = 'ok'
            message = self._format_duration(duration)

        return SamplingStatus(
            state=state,
            message=message,
            timestamp=timestamp,
            duration=duration,
            backend_duration=backend_duration,
            last_error=last_error,
            stale=stale,
            slow=slow,
        )

    @staticmethod
    def _format_duration(duration: float | str) -> str:
        if isinstance(duration, (int, float)):
            return f'{duration:.2f}s'
        return str(duration)

    @staticmethod
    def _invalidate_backend_cache() -> None:
        invalidate = getattr(libascend, 'ascendInvalidateCache', None)
        if invalidate is not None:
            invalidate()
