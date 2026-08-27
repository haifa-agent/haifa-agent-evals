"""Bounded TCP relay for container-local access to a host-only model gateway."""

from __future__ import annotations

import argparse
import asyncio


async def _copy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(64 * 1024):
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()


async def _serve(
    listen_host: str,
    listen_port: int,
    target_host: str,
    target_port: int,
) -> None:
    async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream_reader, upstream_writer = await asyncio.open_connection(target_host, target_port)
        await asyncio.gather(
            _copy(reader, upstream_writer),
            _copy(upstream_reader, writer),
            return_exceptions=True,
        )

    server = await asyncio.start_server(relay, listen_host, listen_port)
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-host", required=True)
    parser.add_argument("--listen-port", required=True, type=int)
    parser.add_argument("--target-host", required=True)
    parser.add_argument("--target-port", required=True, type=int)
    args = parser.parse_args()
    asyncio.run(_serve(args.listen_host, args.listen_port, args.target_host, args.target_port))


if __name__ == "__main__":
    main()
