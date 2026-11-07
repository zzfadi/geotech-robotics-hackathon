"""Bounded recovery for an MCP listener that lost the rapid-restart port race.

Only retries a refused localhost connection. A listening server is never restarted.
This runs after editor startup, not from an agent's level-editing tools.
"""
import errno
import socket
import re


def requested_port(command):
    # Only recover servers explicitly requested by the supported join scripts.
    if not re.search(r'(?:-ModelContextProtocolStartServer\b|bAutoStartServer=True\b)', command, re.I):
        return None
    match = re.search(r'(?:-ModelContextProtocolPort=|ServerPortNumber=)(\d+)', command, re.I)
    port = int(match.group(1)) if match else 8000
    return port if 0 < port < 65536 else None


def listener_refused(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=0.05):
            return False
    except OSError as error:
        return error.errno == errno.ECONNREFUSED


def install():
    import unreal
    from copresence import ui_widgets

    port = requested_port(unreal.SystemLibrary.get_command_line())
    if port is None:
        return
    attempts = 0

    def check():
        nonlocal attempts
        if not listener_refused(port):
            return
        attempts += 1
        unreal.log_warning(f'CoPresence: recovering refused MCP listener on {port} ({attempts}/6)')
        unreal.SystemLibrary.execute_console_command(None, f'ModelContextProtocol.StartServer {port}')
        if attempts < 6:
            ui_widgets.after(10.0, check)

    ui_widgets.after(10.0, check)
