"""Expose Pier's authenticated OpenCode Go tunnel to the Java HTTP client."""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
from contextlib import suppress
from urllib.parse import unquote, urlsplit

_TARGET = b"opencode.ai:443"
_HEADER_LIMIT = 16 * 1024
_HEADER_END = b"\r\n\r\n"
_HANDSHAKE_TIMEOUT = 10


def _parse_proxy(value: str) -> tuple[str, int, bytes]:
    proxy = urlsplit(value)
    if (
        proxy.scheme != "http"
        or not proxy.hostname
        or not proxy.port
        or proxy.username != "agent"
        or not proxy.password
        or proxy.path
        or proxy.query
        or proxy.fragment
    ):
        raise ValueError("Pier HTTPS proxy configuration is invalid")
    auth = base64.b64encode(f"agent:{unquote(proxy.password)}".encode())
    return proxy.hostname, proxy.port, auth


async def _forward(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while data := await reader.read(64 * 1024):
        writer.write(data)
        await writer.drain()
    writer.write_eof()
    await writer.drain()


async def _serve(listen_port: int, upstream: tuple[str, int, bytes]) -> None:
    host, port, auth = upstream

    async def handle(client: asyncio.StreamReader, downstream: asyncio.StreamWriter) -> None:
        proxy: asyncio.StreamWriter | None = None
        try:
            async with asyncio.timeout(_HANDSHAKE_TIMEOUT):
                request = await client.readuntil(_HEADER_END)
                request_line = request.split(b"\r\n", 1)[0].split()
                if request_line not in (
                    [b"CONNECT", _TARGET, b"HTTP/1.0"],
                    [b"CONNECT", _TARGET, b"HTTP/1.1"],
                ):
                    downstream.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                    await downstream.drain()
                    return

                response, proxy = await asyncio.open_connection(host, port, limit=_HEADER_LIMIT)
                proxy.write(
                    b"CONNECT %s HTTP/1.1\r\nHost: %s\r\nProxy-Authorization: Basic %s\r\n\r\n"
                    % (_TARGET, _TARGET, auth)
                )
                await proxy.drain()
                reply = await response.readuntil(_HEADER_END)
                if reply.split(b"\r\n", 1)[0].split()[:2] not in (
                    [b"HTTP/1.0", b"200"],
                    [b"HTTP/1.1", b"200"],
                ):
                    downstream.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                    await downstream.drain()
                    return

                downstream.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await downstream.drain()
            try:
                async with asyncio.TaskGroup() as tunnel:
                    tunnel.create_task(_forward(client, proxy))
                    tunnel.create_task(_forward(response, downstream))
            except* OSError:
                pass
        except (OSError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        finally:
            for writer in (proxy, downstream):
                if writer is not None:
                    writer.close()
                    with suppress(OSError):
                        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", listen_port, limit=_HEADER_LIMIT)
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-port", type=int, default=8317)
    arguments = parser.parse_args()
    try:
        proxy = _parse_proxy(os.environ.get("HTTPS_PROXY", ""))
        asyncio.run(_serve(arguments.listen_port, proxy))
    except (ValueError, OSError):
        raise SystemExit("Cannot start the local OpenCode Go tunnel") from None


if __name__ == "__main__":
    main()
