#!/usr/bin/env python3
"""Synthetic benchmark for legacy GUI sampling vs SnapshotService.

This benchmark does not call real npu-smi or touch NPU memory. It uses fake
devices, processes, and host metrics to compare the old multi-panel sampling
shape with the unified GUI snapshot service.
"""

from __future__ import annotations

import argparse
import contextlib
import statistics
import sys
import time
from dataclasses import dataclass, fields
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Iterable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nputop.api import libascend  # noqa: E402
from nputop.api.utils import NA, Snapshot  # noqa: E402
from nputop.gui import snapshot as snapshot_module  # noqa: E402
from nputop.gui.snapshot import SnapshotService  # noqa: E402


DEFAULT_BENCHMARK_SERVICE_INTERVAL = 2.0


@dataclass
class BenchmarkConfig:
    devices: int
    processes_per_device: int
    iterations: int
    warmup: int
    duration: float
    legacy_panel_interval: float
    legacy_device_ttl: float
    legacy_process_ttl: float
    service_interval: float
    device_delay_ms: float
    process_query_delay_ms: float
    process_snapshot_delay_ms: float
    host_delay_ms: float
    tree_delay_ms: float
    metrics: bool


@dataclass
class Counters:
    device_snapshots: int = 0
    process_queries: int = 0
    process_snapshots: int = 0
    process_snapshot_batches: int = 0
    host_queries: int = 0
    tree_rebuilds: int = 0
    metrics_updates: int = 0

    def reset(self) -> None:
        for field in fields(self):
            setattr(self, field.name, 0)

    @property
    def backend_calls(self) -> int:
        return (
            self.device_snapshots
            + self.process_queries
            + self.process_snapshots
            + self.host_queries
        )


@dataclass
class BenchmarkResult:
    name: str
    durations: list[float]
    counters: Counters

    @property
    def total_ms(self) -> float:
        return 1000.0 * sum(self.durations)

    @property
    def mean_ms(self) -> float:
        return 1000.0 * statistics.mean(self.durations)

    @property
    def median_ms(self) -> float:
        return 1000.0 * statistics.median(self.durations)

    @property
    def p95_ms(self) -> float:
        if len(self.durations) < 2:
            return self.median_ms
        return 1000.0 * statistics.quantiles(self.durations, n=20, method='inclusive')[18]


class FakeProcess:
    def __init__(self, pid: int, device: FakeDevice) -> None:
        self.pid = pid
        self.device = device
        self._ident = (pid, pid, device.index)


class FakeDevice:
    def __init__(self, index: int, counters: Counters, config: BenchmarkConfig) -> None:
        self.index = index
        self.physical_index = index
        self.tuple_index = (index,)
        self.display_index = str(index)
        self.counters = counters
        self.config = config
        self._processes: dict[int, FakeProcess] = {}
        self._snapshot = None

    def mig_devices(self) -> list[FakeDevice]:
        return []

    def as_snapshot(self) -> Snapshot:
        self.counters.device_snapshots += 1
        sleep_ms(self.config.device_delay_ms)
        snapshot = Snapshot(
            real=self,
            index=self.index,
            physical_index=self.physical_index,
            tuple_index=self.tuple_index,
            display_index=self.display_index,
            memory_used=1024 * 1024 * (self.index + 1),
            memory_total=64 * 1024 * 1024 * 1024,
            memory_percent=1.0,
            npu_utilization=10,
        )
        self._snapshot = snapshot
        return snapshot

    @property
    def snapshot(self) -> Snapshot:
        if self._snapshot is None:
            self.as_snapshot()
        return self._snapshot

    def processes(self) -> dict[int, FakeProcess]:
        self.counters.process_queries += 1
        sleep_ms(self.config.process_query_delay_ms)
        return dict(self._processes)


def sleep_ms(milliseconds: float) -> None:
    if milliseconds > 0.0:
        time.sleep(milliseconds / 1000.0)


def make_fixture(config: BenchmarkConfig) -> tuple[Counters, list[FakeDevice], FakeProcess | None]:
    counters = Counters()
    devices = [FakeDevice(index, counters, config) for index in range(config.devices)]
    pid = 1000
    selected = None
    for device in devices:
        for _ in range(config.processes_per_device):
            process = FakeProcess(pid, device)
            device._processes[pid] = process
            selected = selected or process
            pid += 1
    return counters, devices, selected


def fake_take_process_snapshots(
    processes: Iterable[FakeProcess],
    counters: Counters,
    config: BenchmarkConfig,
) -> list[Snapshot]:
    processes = list(processes)
    counters.process_snapshot_batches += 1
    counters.process_snapshots += len(processes)
    sleep_ms(config.process_snapshot_delay_ms * len(processes))
    return [
        Snapshot(
            real=process,
            pid=process.pid,
            _ident=process._ident,
            device=process.device,
            username='user',
            cpu_percent=1.0,
            memory_percent=2.0,
            host_memory=256 * 1024 * 1024,
            npu_memory=512 * 1024 * 1024,
            npu_sm_utilization=NA,
            cpu_percent_string='1.0%',
            memory_percent_string='2.0%',
            running_time_human='1:23',
            command='python benchmark.py',
        )
        for process in processes
    ]


def sample_host(counters: Counters, config: BenchmarkConfig):
    counters.host_queries += 1
    sleep_ms(config.host_delay_ms)
    cpu_percent = 12.5
    counters.host_queries += 1
    sleep_ms(config.host_delay_ms)
    virtual_memory = SimpleNamespace(percent=25.0, used=32 * 1024 * 1024 * 1024)
    counters.host_queries += 1
    sleep_ms(config.host_delay_ms)
    swap_memory = SimpleNamespace(percent=0.0, used=0)
    counters.host_queries += 1
    sleep_ms(config.host_delay_ms)
    load_average = (1.0, 2.0, 3.0)
    return cpu_percent, virtual_memory, swap_memory, load_average


def rebuild_tree(
    process_snapshots: list[Snapshot],
    counters: Counters,
    config: BenchmarkConfig,
) -> None:
    counters.tree_rebuilds += 1
    sleep_ms(config.tree_delay_ms * len(process_snapshots))


@contextlib.contextmanager
def patched_snapshot_runtime(counters: Counters, config: BenchmarkConfig):
    original_take_snapshots = snapshot_module.NpuProcess.take_snapshots
    original_cpu_percent = snapshot_module.host.cpu_percent
    original_virtual_memory = snapshot_module.host.virtual_memory
    original_swap_memory = snapshot_module.host.swap_memory
    original_load_average = snapshot_module.host.load_average
    original_cache_stats = snapshot_module.libascend.ascendGetCacheStats

    def take_snapshots(processes, *, failsafe=False):
        return fake_take_process_snapshots(processes, counters, config)

    def cpu_percent():
        counters.host_queries += 1
        sleep_ms(config.host_delay_ms)
        return 12.5

    def virtual_memory():
        counters.host_queries += 1
        sleep_ms(config.host_delay_ms)
        return SimpleNamespace(percent=25.0, used=32 * 1024 * 1024 * 1024)

    def swap_memory():
        counters.host_queries += 1
        sleep_ms(config.host_delay_ms)
        return SimpleNamespace(percent=0.0, used=0)

    def load_average():
        counters.host_queries += 1
        sleep_ms(config.host_delay_ms)
        return (1.0, 2.0, 3.0)

    def cache_stats():
        return libascend.CacheStats(
            cache_ttl=0.5,
            npusmi_timeout=3.0,
            last_update_wall_ts=time.time(),
            last_update_duration=0.0,
            last_update_error=NA,
            cache_size=config.devices,
        )

    snapshot_module.NpuProcess.take_snapshots = staticmethod(take_snapshots)
    snapshot_module.host.cpu_percent = cpu_percent
    snapshot_module.host.virtual_memory = virtual_memory
    snapshot_module.host.swap_memory = swap_memory
    snapshot_module.host.load_average = load_average
    snapshot_module.libascend.ascendGetCacheStats = cache_stats
    try:
        yield
    finally:
        snapshot_module.NpuProcess.take_snapshots = original_take_snapshots
        snapshot_module.host.cpu_percent = original_cpu_percent
        snapshot_module.host.virtual_memory = original_virtual_memory
        snapshot_module.host.swap_memory = original_swap_memory
        snapshot_module.host.load_average = original_load_average
        snapshot_module.libascend.ascendGetCacheStats = original_cache_stats


def legacy_iteration(
    devices: list[FakeDevice],
    selected: FakeProcess | None,
    counters: Counters,
    config: BenchmarkConfig,
) -> None:
    [device.as_snapshot() for device in devices]
    sample_host(counters, config)
    processes = [process for device in devices for process in device.processes().values()]
    process_snapshots = fake_take_process_snapshots(processes, counters, config)
    rebuild_tree(process_snapshots, counters, config)
    if config.metrics and selected is not None:
        counters.metrics_updates += 1
        selected.device.as_snapshot()
        selected.device.processes()
        fake_take_process_snapshots([selected], counters, config)


def service_iteration(
    service: SnapshotService,
    counters: Counters,
    config: BenchmarkConfig,
) -> None:
    bundle = service.collect()
    rebuild_tree(bundle.processes, counters, config)
    if config.metrics and bundle.processes:
        counters.metrics_updates += 1
        service.process_snapshot_for(bundle.processes[0])


def steps_for(duration: float, interval: float) -> int:
    return int(duration / interval) + 1


def legacy_steady_workload(
    devices: list[FakeDevice],
    selected: FakeProcess | None,
    counters: Counters,
    config: BenchmarkConfig,
) -> None:
    last_device = -float('inf')
    last_process = -float('inf')
    last_tree = -float('inf')
    process_snapshots = []
    for step in range(steps_for(config.duration, config.legacy_panel_interval)):
        now = step * config.legacy_panel_interval
        if now - last_device >= config.legacy_device_ttl:
            [device.as_snapshot() for device in devices]
            last_device = now

        sample_host(counters, config)

        if now - last_process >= config.legacy_process_ttl:
            processes = [process for device in devices for process in device.processes().values()]
            process_snapshots = fake_take_process_snapshots(processes, counters, config)
            last_process = now

        if now - last_tree >= config.legacy_process_ttl:
            rebuild_tree(process_snapshots, counters, config)
            last_tree = now

        if config.metrics and selected is not None:
            counters.metrics_updates += 1
            selected.device.as_snapshot()
            selected.device.processes()
            fake_take_process_snapshots([selected], counters, config)


def service_steady_workload(
    service: SnapshotService,
    counters: Counters,
    config: BenchmarkConfig,
) -> None:
    for _ in range(steps_for(config.duration, config.service_interval)):
        service_iteration(service, counters, config)


def run_case(
    name: str,
    config: BenchmarkConfig,
    make_runner: Callable[[list[FakeDevice], FakeProcess | None, Counters], Callable[[], None]],
) -> BenchmarkResult:
    counters, devices, selected = make_fixture(config)
    with patched_snapshot_runtime(counters, config):
        runner = make_runner(devices, selected, counters)
        for _ in range(config.warmup):
            runner()
        counters.reset()

        durations = []
        for _ in range(config.iterations):
            start = time.perf_counter()
            runner()
            durations.append(time.perf_counter() - start)

    return BenchmarkResult(name=name, durations=durations, counters=counters)


def print_results(
    title: str,
    config: BenchmarkConfig,
    legacy: BenchmarkResult,
    service: BenchmarkResult,
) -> None:
    headers = [
        'case',
        'total ms',
        'mean ms',
        'p50 ms',
        'p95 ms',
        'backend',
        'batches',
        'dev',
        'proc q',
        'proc snap',
        'host',
        'tree',
        'metrics',
    ]
    rows = []
    for result in (legacy, service):
        counters = result.counters
        rows.append(
            [
                result.name,
                f'{result.total_ms:.1f}',
                f'{result.mean_ms:.2f}',
                f'{result.median_ms:.2f}',
                f'{result.p95_ms:.2f}',
                str(counters.backend_calls),
                str(counters.process_snapshot_batches),
                str(counters.device_snapshots),
                str(counters.process_queries),
                str(counters.process_snapshots),
                str(counters.host_queries),
                str(counters.tree_rebuilds),
                str(counters.metrics_updates),
            ],
        )

    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]

    print(
        f'{title}\n'
        'Synthetic GUI sampling benchmark '
        f'({config.devices} devices, '
        f'{config.processes_per_device} processes/device, '
        f'{config.iterations} iterations, '
        f'metrics={"on" if config.metrics else "off"})',
    )
    print(' '.join(header.rjust(widths[index]) for index, header in enumerate(headers)))
    print(' '.join('-' * width for width in widths))
    for row in rows:
        print(' '.join(row[index].rjust(widths[index]) for index in range(len(headers))))

    wall_speedup = legacy.total_ms / service.total_ms if service.total_ms else float('inf')
    backend_reduction = (
        1.0 - service.counters.backend_calls / legacy.counters.backend_calls
        if legacy.counters.backend_calls
        else 0.0
    )
    print()
    print(f'wall-time speedup: {wall_speedup:.2f}x')
    print(f'backend-call reduction: {backend_reduction:.1%}')
    print()


def parse_args() -> BenchmarkConfig:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--devices', type=int, default=16)
    parser.add_argument('--processes-per-device', type=int, default=8)
    parser.add_argument('--iterations', type=int, default=30)
    parser.add_argument('--warmup', type=int, default=3)
    parser.add_argument('--duration', type=float, default=6.0)
    parser.add_argument('--legacy-panel-interval', type=float, default=0.5)
    parser.add_argument('--legacy-device-ttl', type=float, default=1.0)
    parser.add_argument('--legacy-process-ttl', type=float, default=2.0)
    parser.add_argument('--service-interval', type=float, default=DEFAULT_BENCHMARK_SERVICE_INTERVAL)
    parser.add_argument('--device-delay-ms', type=float, default=0.2)
    parser.add_argument('--process-query-delay-ms', type=float, default=0.5)
    parser.add_argument('--process-snapshot-delay-ms', type=float, default=0.02)
    parser.add_argument('--host-delay-ms', type=float, default=0.05)
    parser.add_argument('--tree-delay-ms', type=float, default=0.005)
    parser.add_argument('--no-metrics', action='store_true')
    args = parser.parse_args()

    return BenchmarkConfig(
        devices=args.devices,
        processes_per_device=args.processes_per_device,
        iterations=args.iterations,
        warmup=args.warmup,
        duration=args.duration,
        legacy_panel_interval=args.legacy_panel_interval,
        legacy_device_ttl=args.legacy_device_ttl,
        legacy_process_ttl=args.legacy_process_ttl,
        service_interval=args.service_interval,
        device_delay_ms=args.device_delay_ms,
        process_query_delay_ms=args.process_query_delay_ms,
        process_snapshot_delay_ms=args.process_snapshot_delay_ms,
        host_delay_ms=args.host_delay_ms,
        tree_delay_ms=args.tree_delay_ms,
        metrics=not args.no_metrics,
    )


def main() -> int:
    config = parse_args()

    def make_service_runner(devices, selected, counters):  # pylint: disable=unused-argument
        service = SnapshotService(devices, interval=config.service_interval)
        return lambda: service_iteration(service, counters, config)

    def make_steady_service_runner(devices, selected, counters):  # pylint: disable=unused-argument
        service = SnapshotService(devices, interval=config.service_interval)
        return lambda: service_steady_workload(service, counters, config)

    forced_legacy = run_case(
        'legacy panels',
        config,
        lambda devices, selected, counters: lambda: legacy_iteration(
            devices,
            selected,
            counters,
            config,
        ),
    )
    forced_service = run_case(
        'snapshot service',
        config,
        make_service_runner,
    )
    print_results('Forced refresh cost', config, forced_legacy, forced_service)

    steady_legacy = run_case(
        'legacy panels',
        config,
        lambda devices, selected, counters: lambda: legacy_steady_workload(
            devices,
            selected,
            counters,
            config,
        ),
    )
    steady_service = run_case(
        'snapshot service',
        config,
        make_steady_service_runner,
    )
    print_results(
        (
            'Steady-state sampler load '
            f'({config.duration:g}s simulated, '
            f'legacy panel={config.legacy_panel_interval:g}s, '
            f'legacy process TTL={config.legacy_process_ttl:g}s, '
            f'service interval={config.service_interval:g}s)'
        ),
        config,
        steady_legacy,
        steady_service,
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
