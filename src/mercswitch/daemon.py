from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .client import MercSwitchClient
from .daemon_config import DaemonSettings
from .models import CandidateConfig, OperationResult, SwitchMetrics, SwitchState
from .snmp_agent import SnmpAgent, SnmpSystemConfig
from .ssh_server import start_ssh_server
from .storage import CacheStore


class LockedDaemonClient:
    def __init__(self, daemon: MercSwitchDaemon) -> None:
        self.daemon = daemon
        self.store = daemon.store

    async def commit(self, target: CandidateConfig, **kwargs: Any) -> OperationResult:
        async with self.daemon.io_lock:
            result = await self.daemon.client.commit(target, **kwargs)
            if result.final_state:
                self.daemon.state = result.final_state
            return result

    async def write_memory(self) -> None:
        async with self.daemon.io_lock:
            await self.daemon.client.write_memory()


class MercSwitchDaemon:
    def __init__(self, settings: DaemonSettings) -> None:
        self.settings = settings
        self.store = CacheStore(Path(settings.data_dir))
        self.client = MercSwitchClient(
            settings.device.url,
            settings.device.username,
            settings.device.password(),
            cache_dir=settings.data_dir,
            verify_tls=settings.device.verify_tls,
        )
        self.client_proxy = LockedDaemonClient(self)
        self.io_lock = asyncio.Lock()
        self.state: SwitchState | None = None
        self.metrics: SwitchMetrics | None = None
        self.snmp = SnmpAgent(
            settings.snmp.host,
            settings.snmp.port,
            settings.snmp.community(),
        )
        self.ssh_server = None
        self.tasks: list[asyncio.Task] = []
        self.stop_event = asyncio.Event()
        self.detail_port = 1

    async def start(self) -> None:
        async with self.io_lock:
            self.state = await self.client.pull()
            self.metrics = await self.client.adapter.read_metrics(self.detail_port)
        self._update_snmp()
        await self.snmp.start()
        self.ssh_server = await start_ssh_server(self)
        self.tasks = [
            asyncio.create_task(self._summary_loop(), name="summary-poller"),
            asyncio.create_task(self._detail_loop(), name="detail-poller"),
        ]
        self._write_health("ok")

    def _update_snmp(self) -> None:
        if self.state and self.metrics:
            self.snmp.update(
                self.state,
                self.metrics,
                SnmpSystemConfig(
                    self.settings.snmp.name,
                    self.settings.snmp.contact,
                    self.settings.snmp.location,
                ),
            )

    def _write_health(self, status: str, error: str = "") -> None:
        self.store.write_health(
            {
                "status": status,
                "error": error,
                "updated_at": datetime.now(UTC).isoformat(),
                "state_observed_at": self.state.observed_at if self.state else None,
                "metrics_observed_at": self.metrics.observed_at if self.metrics else None,
            }
        )

    async def _summary_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(), timeout=self.settings.poll.summary_interval
                )
                continue
            except TimeoutError:
                pass
            try:
                async with self.io_lock:
                    self.state = await self.client.pull()
                    fresh = await self.client.adapter.read_metrics()
                if self.metrics:
                    for index, metric in fresh.ports.items():
                        previous = self.metrics.ports.get(index)
                        if previous:
                            detail_fields = (
                                "rx_unicast",
                                "rx_multicast",
                                "rx_broadcast",
                                "tx_unicast",
                                "tx_multicast",
                                "tx_broadcast",
                                "undersize",
                                "oversize",
                                "crc_errors",
                                "fragments",
                                "jabbers",
                                "collisions",
                                "detail_available",
                            )
                            for field in detail_fields:
                                setattr(metric, field, getattr(previous, field))
                self.metrics = fresh
                self._update_snmp()
                self._write_health("ok")
            except Exception as exc:
                self._write_health("degraded", str(exc))

    async def _detail_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(), timeout=self.settings.poll.detail_interval
                )
                continue
            except TimeoutError:
                pass
            try:
                async with self.io_lock:
                    detailed = await self.client.adapter.read_metrics(self.detail_port)
                if self.metrics and self.detail_port in detailed.ports:
                    self.metrics.ports[self.detail_port] = detailed.ports[self.detail_port]
                    self.metrics.observed_at = detailed.observed_at
                else:
                    self.metrics = detailed
                if self.state:
                    self.detail_port = self.detail_port % self.state.capabilities.port_count + 1
                self._update_snmp()
                self._write_health("ok")
            except Exception as exc:
                self._write_health("degraded", str(exc))

    async def run(self) -> None:
        await self.start()
        await self.stop_event.wait()

    async def close(self) -> None:
        self.stop_event.set()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.ssh_server:
            self.ssh_server.close()
            await self.ssh_server.wait_closed()
        self.snmp.close()
        await self.client.close()
