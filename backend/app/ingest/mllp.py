"""Minimal MLLP listener for HL7v2 feeds from an interface engine.

Framing: <VT> message <FS><CR>. Each message gets an ACK in the same framing.
Off by default (HL7_MLLP_PORT=0) and bound to 127.0.0.1; MLLP has no auth of
its own, so expose it only behind a VPN or the interface engine's tunnel.
"""

from __future__ import annotations

import asyncio
import logging

from .hl7 import MLLP_END, MLLP_START
from .service import handle_hl7

log = logging.getLogger("continuum.ingest.mllp")


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    try:
        while True:
            data = await reader.readuntil(b"\x1c\x0d")
            message = data.decode("utf-8", errors="replace")
            result = await asyncio.to_thread(handle_hl7, message, actor="HL7 interface (MLLP)")
            writer.write(f"{MLLP_START}{result['ack']}{MLLP_END}".encode())
            await writer.drain()
            log.info("MLLP %s from %s: %s", result.get("control_id"), peer, result.get("outcome"))
    except (asyncio.IncompleteReadError, ConnectionResetError):
        pass
    finally:
        writer.close()


async def serve(host: str, port: int) -> None:
    server = await asyncio.start_server(_handle, host, port)
    log.info("MLLP listener on %s:%d", host, port)
    async with server:
        await server.serve_forever()
