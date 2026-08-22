#!/usr/bin/env python3
"""Small TCP relay used inside the Podman VM.

The SSH reverse tunnel listens only on VM loopback. This relay exposes that endpoint to
containers through host.containers.internal without exposing the Windows proxy directly.
"""

from __future__ import annotations

import argparse
import asyncio


async def _forward(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(64 * 1024):
            writer.write(chunk)
            await writer.drain()
    finally:
        writer.close()


async def _handle(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    target_host: str,
    target_port: int,
) -> None:
    try:
        target_reader, target_writer = await asyncio.open_connection(target_host, target_port)
    except OSError:
        client_writer.close()
        await client_writer.wait_closed()
        return
    await asyncio.gather(
        _forward(client_reader, target_writer),
        _forward(target_reader, client_writer),
        return_exceptions=True,
    )


async def _serve(listen_port: int, target_host: str, target_port: int) -> None:
    server = await asyncio.start_server(
        lambda reader, writer: _handle(reader, writer, target_host, target_port),
        host="0.0.0.0",
        port=listen_port,
    )
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-port", type=int, required=True)
    parser.add_argument("--target-host", default="127.0.0.1")
    parser.add_argument("--target-port", type=int, required=True)
    args = parser.parse_args()
    asyncio.run(_serve(args.listen_port, args.target_host, args.target_port))


if __name__ == "__main__":
    main()
