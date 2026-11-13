"""RH-08D-03: the discovery egress proxy, a CONNECT relay to exactly two destinations.

It runs as its own container from the Research API image (``python -m app.discovery_egress_proxy``). It is
the only staging container with outbound internet access; the Research API container stays on an internal
network and reaches the providers only through it. It allows:

* ``CONNECT api.openalex.org:443`` and ``CONNECT api.crossref.org:443`` and nothing else (no plain HTTP, no
  other host or port, no absolute-URI requests);
* only when every resolved address is public, connecting to a vetted address, so a DNS answer cannot steer
  it at the host, the Docker networks or a metadata service.

TLS stays end to end between the API and the provider (the proxy relays bytes and sees no URL, header or
API key). Each tunnel has idle and total time limits and a byte cap, concurrent tunnels are capped, and the
log line records only the destination host, the outcome, byte counts and duration.

    RESEARCH_EGRESS_LISTEN=0.0.0.0:3128 python -m app.discovery_egress_proxy
"""
import asyncio
import ipaddress
import logging
import os
import socket
import time

ALLOWED = frozenset({"api.openalex.org", "api.crossref.org"})
PORT = 443
MAX_HEADER_BYTES = 4096
HEADER_TIMEOUT = 5.0
CONNECT_TIMEOUT = 10.0
IDLE_TIMEOUT = 20.0
TUNNEL_SECONDS = 60.0
TUNNEL_BYTES = 8_000_000
MAX_TUNNELS = 8

log = logging.getLogger("discovery-egress")


async def system_resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, PORT, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


async def system_open(address: str):
    return await asyncio.open_connection(address, PORT)


class EgressProxy:
    def __init__(self, *, resolve=system_resolve, open_upstream=system_open, clock=time.monotonic,
                 max_tunnels: int = MAX_TUNNELS):
        self._resolve, self._open, self._clock = resolve, open_upstream, clock
        self._slots = asyncio.Semaphore(max_tunnels)
        self.active = 0

    async def _reply(self, writer, status: str, outcome: str, host: str, started: float):
        writer.write(f"HTTP/1.1 {status}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n".encode())
        await writer.drain()
        writer.close()
        log.info("host=%s outcome=%s ms=%d", host or "-", outcome, (self._clock() - started) * 1000)
        return outcome

    async def handle(self, reader, writer) -> str:
        started = self._clock()
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEADER_TIMEOUT)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
            return await self._reply(writer, "400 Bad Request", "bad-request", "", started)
        if len(head) > MAX_HEADER_BYTES:
            return await self._reply(writer, "431 Request Header Fields Too Large", "bad-request", "", started)
        parts = head.split(b"\r\n", 1)[0].decode("latin-1").split(" ")
        if len(parts) != 3 or parts[0] != "CONNECT" or not parts[2].startswith("HTTP/1."):
            return await self._reply(writer, "405 Method Not Allowed", "not-connect", "", started)
        host, _, port = parts[1].rpartition(":")
        host = host.lower()
        if host not in ALLOWED or port != str(PORT):
            return await self._reply(writer, "403 Forbidden", "destination-not-allowed", "", started)
        if self._slots.locked():
            return await self._reply(writer, "503 Service Unavailable", "busy", host, started)
        async with self._slots:
            return await self._tunnel(reader, writer, host, started)

    async def _tunnel(self, reader, writer, host: str, started: float) -> str:
        try:
            addresses = await asyncio.wait_for(self._resolve(host), CONNECT_TIMEOUT)
        except (OSError, asyncio.TimeoutError):
            return await self._reply(writer, "502 Bad Gateway", "dns-failure", host, started)
        if not addresses or any(not ipaddress.ip_address(a).is_global or ipaddress.ip_address(a).is_multicast
                                for a in addresses):
            return await self._reply(writer, "403 Forbidden", "non-public-address", host, started)
        address = next((a for a in addresses if ":" not in a), addresses[0])
        try:
            up_reader, up_writer = await asyncio.wait_for(self._open(address), CONNECT_TIMEOUT)
        except (OSError, asyncio.TimeoutError):
            return await self._reply(writer, "502 Bad Gateway", "connect-failure", host, started)
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        self.active += 1
        counts = {"up": 0, "down": 0}
        deadline = started + TUNNEL_SECONDS

        async def pipe(src, dst, key):
            while True:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                chunk = await asyncio.wait_for(src.read(65536), min(IDLE_TIMEOUT, remaining))
                if not chunk:
                    return
                counts[key] += len(chunk)
                if counts["up"] + counts["down"] > TUNNEL_BYTES:
                    raise OverflowError
                dst.write(chunk)
                await dst.drain()

        outcome = "closed"
        tasks = [asyncio.ensure_future(pipe(reader, up_writer, "up")),
                 asyncio.ensure_future(pipe(up_reader, writer, "down"))]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                error = task.exception()
                if isinstance(error, asyncio.TimeoutError):
                    outcome = "timeout"
                elif isinstance(error, OverflowError):
                    outcome = "byte-cap"
                elif error is not None:
                    outcome = "connection-error"
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for w in (up_writer, writer):
                w.close()
            self.active -= 1
        log.info("host=%s outcome=%s up=%d down=%d ms=%d", host, outcome, counts["up"], counts["down"],
                 (self._clock() - started) * 1000)
        return outcome


async def serve(listen: str) -> None:
    host, _, port = listen.rpartition(":")
    proxy = EgressProxy()
    server = await asyncio.start_server(proxy.handle, host or "0.0.0.0", int(port), limit=MAX_HEADER_BYTES)
    log.info("listening on %s; allowed=%s", listen, ",".join(sorted(ALLOWED)))
    async with server:
        await server.serve_forever()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(serve(os.getenv("RESEARCH_EGRESS_LISTEN", "0.0.0.0:3128")))


if __name__ == "__main__":
    main()
