# /// script
# requires-python = ">=3.11"
# dependencies = ["websockets>=15,<17"]
# ///
"""Carries a co-presence session over a web address, so people and agents join the
always-on server at https://multiunreal.geotech-hub.com from anywhere, without a VPN.
Multi-User talks UDP, which Cloudflare's tunnels don't carry, so this relay puts each
editor's UDP traffic, and each connection to the session hub, in a WebSocket, which they do.

On the server:   uv run --script Tools/copresence_relay.py serve --code-file PATH [--port 8795]
On each joiner:  uv run --script Tools/copresence_relay.py connect https://multiunreal.geotech-hub.com --code CODE

The joiner's end listens where a session on the same machine would be, 127.0.0.1:9030 for
Multi-User and 127.0.0.1:8790 for the hub, so editors and agents join 127.0.0.1. The server's
end passes them on to the Multi-User server and the hub, which listen only on the server
machine itself. Only people with the session code get through.
"""

import argparse
import asyncio
import hmac
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from websockets.asyncio.client import connect as ws_connect
from websockets.asyncio.server import serve as ws_serve
from websockets.exceptions import ConnectionClosed

CODE_HEADER = "X-CoPresence-Code"
MULTI_USER_PORT = 9030
HUB_PORT = 8790
RELAY_PORT = 8795
# A UDP datagram is at most this big, and a TCP chunk is read this big
MAX_MESSAGE = 65536
# Datagrams waiting for a slow connection are dropped past this, as UDP would
QUEUE_LIMIT = 4096
# A joiner's editor that has sent and received nothing for this long has gone
IDLE_SECONDS = 300
CHECK_TIMEOUT_SECONDS = 15


def _log(text: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}", flush=True)


async def _until_first_done(*coroutines) -> None:
    """Runs the coroutines until one of them finishes, then stops the others."""
    tasks = [asyncio.ensure_future(coroutine) for coroutine in coroutines]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class _Datagrams(asyncio.DatagramProtocol):
    """Hands each datagram a socket receives to a callback."""

    def __init__(self, on_datagram):
        self.on_datagram = on_datagram

    def datagram_received(self, data: bytes, address) -> None:
        self.on_datagram(data, address)


def _queue_put(queue: asyncio.Queue, data: bytes) -> None:
    if queue.qsize() < QUEUE_LIMIT:
        queue.put_nowait(data)


# Server end -----------------------------------------------------------------

async def _serve_udp(connection, port: int) -> None:
    """One joiner's editor: its own UDP socket to the Multi-User server, so the server tells
    the editors apart and answers each on its own."""
    loop = asyncio.get_running_loop()
    from_server: asyncio.Queue = asyncio.Queue()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _Datagrams(lambda data, address: _queue_put(from_server, data)),
        local_addr=("127.0.0.1", 0), remote_addr=("127.0.0.1", port))

    async def to_server() -> None:
        async for message in connection:
            if isinstance(message, bytes):
                transport.sendto(message)

    async def to_editor() -> None:
        while True:
            await connection.send(await from_server.get())

    try:
        await _until_first_done(to_server(), to_editor())
    finally:
        transport.close()


async def _serve_tcp(connection, port: int) -> None:
    """One connection to the session hub, such as Agent Chat's long poll."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)

    async def to_hub() -> None:
        async for message in connection:
            writer.write(message if isinstance(message, bytes) else message.encode())
            await writer.drain()

    async def to_joiner() -> None:
        while data := await reader.read(MAX_MESSAGE):
            await connection.send(data)

    try:
        await _until_first_done(to_hub(), to_joiner())
    finally:
        writer.close()


async def serve(arguments) -> None:
    code_file = Path(arguments.code_file)

    def code_matches(given: str) -> bool:
        # Read each time, so a new code takes effect without a restart
        expected = code_file.read_text().strip().upper() if code_file.exists() else ""
        return bool(expected) and hmac.compare_digest(given.strip().upper(), expected)

    def process_request(connection, request):
        if request.path == "/health":
            return connection.respond(200, "CoPresence relay\n")
        if request.path not in ("/udp", "/tcp", "/check"):
            return connection.respond(404, "Not found\n")
        if not code_matches(request.headers.get(CODE_HEADER, "")):
            return connection.respond(403, "Wrong session code\n")
        if request.path == "/check":
            return connection.respond(200, "OK\n")
        return None

    async def handler(connection) -> None:
        try:
            if connection.request.path == "/udp":
                await _serve_udp(connection, arguments.multi_user_port)
            else:
                await _serve_tcp(connection, arguments.hub_port)
        except (ConnectionClosed, OSError) as error:
            if not isinstance(error, ConnectionClosed):
                _log(f"relay error: {error}")

    async with ws_serve(handler, "127.0.0.1", arguments.port, process_request=process_request, max_size=MAX_MESSAGE):
        _log(f"relay serving on 127.0.0.1:{arguments.port}")
        await asyncio.get_running_loop().create_future()


# Joiner end -----------------------------------------------------------------

class _EditorLink:
    """The WebSocket for one local editor's UDP traffic, opened on its first datagram."""

    def __init__(self, url: str, headers: dict, transport, address, on_done):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.last_traffic = time.monotonic()
        self.task = asyncio.ensure_future(self._run(url, headers, transport, address, on_done))

    def send(self, data: bytes) -> None:
        self.last_traffic = time.monotonic()
        _queue_put(self.queue, data)

    async def _run(self, url: str, headers: dict, transport, address, on_done) -> None:
        try:
            async with ws_connect(url, additional_headers=headers, max_size=MAX_MESSAGE, open_timeout=CHECK_TIMEOUT_SECONDS) as connection:
                async def to_relay() -> None:
                    while True:
                        await connection.send(await self.queue.get())

                async def to_editor() -> None:
                    async for message in connection:
                        self.last_traffic = time.monotonic()
                        transport.sendto(message, address)

                async def watch_idle() -> None:
                    while time.monotonic() - self.last_traffic < IDLE_SECONDS:
                        await asyncio.sleep(IDLE_SECONDS / 10)

                await _until_first_done(to_relay(), to_editor(), watch_idle())
        except (ConnectionClosed, OSError, TimeoutError) as error:
            _log(f"session connection for {address[0]}:{address[1]} ended: {error}")
        finally:
            on_done(address)


def _check(url: str, code: str) -> None:
    """Fails early, with a reason a person can act on, when the relay can't be used."""
    request = urllib.request.Request(f"{url}/check", headers={CODE_HEADER: code, "User-Agent": "CoPresenceRelay"})
    try:
        urllib.request.urlopen(request, timeout=CHECK_TIMEOUT_SECONDS).close()
    except urllib.error.HTTPError as error:
        sys.exit("Wrong session code." if error.code == 403 else f"The session's relay answered {error.code}.")
    except (urllib.error.URLError, OSError) as error:
        sys.exit(f"Can't reach {url}: {error}")


async def connect(arguments) -> None:
    url = arguments.url.rstrip("/")
    if "://" not in url:
        url = f"https://{url}"
    code = arguments.code.strip().upper()
    _check(url, code)
    websocket_url = url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    headers = {CODE_HEADER: code}
    loop = asyncio.get_running_loop()
    links: dict = {}

    def on_datagram(data: bytes, address) -> None:
        link = links.get(address)
        if link is None:
            link = links[address] = _EditorLink(f"{websocket_url}/udp", headers, transport, address, links.pop)
        link.send(data)

    transport, _ = await loop.create_datagram_endpoint(lambda: _Datagrams(on_datagram),
                                                       local_addr=("127.0.0.1", arguments.udp_port))

    async def on_hub_connection(reader, writer) -> None:
        try:
            async with ws_connect(f"{websocket_url}/tcp", additional_headers=headers, max_size=MAX_MESSAGE,
                                  open_timeout=CHECK_TIMEOUT_SECONDS) as connection:
                async def to_relay() -> None:
                    while data := await reader.read(MAX_MESSAGE):
                        await connection.send(data)
                    # The request is complete; wait for the answer
                    await asyncio.get_running_loop().create_future()

                async def to_local() -> None:
                    async for message in connection:
                        writer.write(message if isinstance(message, bytes) else message.encode())
                        await writer.drain()

                await _until_first_done(to_relay(), to_local())
        except (ConnectionClosed, OSError, TimeoutError):
            pass
        finally:
            writer.close()

    hub = await asyncio.start_server(on_hub_connection, "127.0.0.1", arguments.hub_port)
    _log(f"relay to {url}: Multi-User on 127.0.0.1:{arguments.udp_port}, hub on 127.0.0.1:{arguments.hub_port}")
    async with hub:
        await hub.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    serving = commands.add_parser("serve", help="On the server: pass the relay's connections to Multi-User and the hub")
    serving.add_argument("--code-file", required=True, help="The hub's session code file; only that code gets through")
    serving.add_argument("--port", type=int, default=RELAY_PORT)
    serving.add_argument("--multi-user-port", type=int, default=MULTI_USER_PORT)
    serving.add_argument("--hub-port", type=int, default=HUB_PORT)
    connecting = commands.add_parser("connect", help="On a joiner's machine: listen locally and relay to the server")
    connecting.add_argument("url", help="The session's address, like https://multiunreal.geotech-hub.com")
    connecting.add_argument("--code", required=True, help="The session code")
    connecting.add_argument("--udp-port", type=int, default=MULTI_USER_PORT)
    connecting.add_argument("--hub-port", type=int, default=HUB_PORT)
    arguments = parser.parse_args()
    # Windows' default event loop stops reading a UDP socket for good once a datagram has gone to a
    # local port that closed, as an editor's does when it quits, which left every editor started
    # after that unable to find the session. The selector loop just reports it and reads on
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            runner.run(serve(arguments) if arguments.command == "serve" else connect(arguments))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
