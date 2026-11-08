"""pickup_server startup order, on a fake arm (no USB, CAN or motors).

    bash arm/tools/arm test arm/tools/test_pickup_server_startup.py

The server must claim 127.0.0.1:<port> before it looks for adapters, builds
the arm, connects or enables it: a busy port exits non-zero with the motors
untouched. GsUsbBackend, resolve and Arm are replaced, so nothing here can
reach hardware even if the order regresses.
"""
import json
import math
import os
import socket
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import pickup_server as ps
from maker_arm.arm import ArmState

TOOLS = Path(__file__).resolve().parent


class Recorder:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()
        self.serving = threading.Event()

    def add(self, what):
        with self.lock:
            self.calls.append(what)


def fake_arm_class(rec):
    class FakeArm:
        def __init__(self, cfg, backend):
            rec.add('construct')
            self.config = cfg
            self.state = ArmState.CONNECTED
            self.fault_reason = None
            self.unconfirmed_disable = []
            fb = NS(torque=0., temperature=30., velocity=0.)
            self.motors = [NS(feedback=fb, feedback_age=.01) for _ in range(7)]
            self._q = [math.radians(x) for x in (-16.5, -55.6, 20.9, 44.1, 10.25, 1.46, -90.)]

        def connect(self):
            rec.add('connect')

        def enable(self):
            rec.add('enable')
            self.state = ArmState.ENABLED

        def get_joint_positions(self):
            return list(self._q)

        def get_commanded_positions(self):
            return list(self._q)

        def hold_current_position(self):
            rec.add('hold')

        def disable(self):
            rec.add('disable')
            self.state = ArmState.CONNECTED

        def disconnect(self):
            rec.add('disconnect')

    return FakeArm


@pytest.fixture
def fake_hw(monkeypatch):
    rec = Recorder()

    def backend(serial):
        rec.add('backend')
        return NS(serial=serial)

    def resolve(which):
        rec.add('resolve')
        return 'right', 'FAKE0001'

    class RecordingTCPServer(socketserver.TCPServer):
        def server_bind(self):
            super().server_bind()
            rec.add('bind')

        def serve_forever(self, *a, **k):
            rec.add('serve')
            rec.serving.set()
            super().serve_forever(*a, **k)

        def server_close(self):
            super().server_close()
            rec.add('server_close')

    monkeypatch.setattr(ps, 'GsUsbBackend', backend)
    monkeypatch.setattr(ps, 'resolve', resolve)
    monkeypatch.setattr(ps, 'Arm', fake_arm_class(rec))
    monkeypatch.setattr(ps, 'config', lambda: NS(joints=[]))
    monkeypatch.setattr(ps.socketserver, 'TCPServer', RecordingTCPServer)
    return rec


def busy_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    s.listen(1)
    return s, s.getsockname()[1]


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_busy_port_exits_nonzero_before_touching_the_arm(fake_hw, tmp_path, capsys):
    holder, port = busy_port()
    try:
        code = ps.main(['right', '--operator-cutoff', '--port', str(port), '--log', str(tmp_path / 's.jsonl')])
    finally:
        holder.close()
    assert code not in (0, None)
    assert 'PORT_BUSY' in capsys.readouterr().err
    for touched in ('resolve', 'backend', 'construct', 'connect', 'enable'):
        assert touched not in fake_hw.calls, fake_hw.calls
    assert fake_hw.calls.count('connect') == 0 and fake_hw.calls.count('enable') == 0


def test_happy_path_binds_then_connects_then_enables(fake_hw, tmp_path, capsys):
    port = free_port()
    log = tmp_path / 's.jsonl'
    result = {}
    t = threading.Thread(target=lambda: result.update(code=ps.main(
        ['right', '--operator-cutoff', '--port', str(port), '--log', str(log)])), daemon=True)
    t.start()
    # Connect once it serves (after READY); earlier connections are dropped.
    assert fake_hw.serving.wait(10), 'server never started serving'
    with socket.create_connection(('127.0.0.1', port), timeout=10) as s:
        s.sendall(b'{"cmd": "status"}\n{"cmd": "release_supported"}\n')
        replies = s.makefile().read().splitlines()
    t.join(10)
    assert not t.is_alive(), 'server did not exit after release_supported'
    status, released = (json.loads(r) for r in replies)
    assert status['ok'] and status['arm_state'] == 'ENABLED'
    assert released == {'ok': True, 'release_acknowledged': True}
    order = [c for c in fake_hw.calls if c in ('bind', 'resolve', 'construct', 'connect', 'enable', 'serve')]
    assert order == ['bind', 'resolve', 'construct', 'connect', 'enable', 'serve']
    assert fake_hw.calls[-1] == 'server_close'
    assert 'READY right port %d' % port in capsys.readouterr().out
    assert result.get('code') in (0, None)
    events = [json.loads(line)['event'] for line in log.read_text().splitlines()]
    assert events[:2] == ['connect', 'enabled'] and 'release_supported' in events


def test_a_move_sent_before_ready_never_runs(fake_hw, tmp_path, monkeypatch, capsys):
    """The port is claimed before the arm starts, but a client that connects in
    that window is dropped unserved, as when the port was not open yet."""
    gate = threading.Event()

    class SlowEnableArm(ps.Arm):
        def enable(self):
            assert gate.wait(10)
            super().enable()

    monkeypatch.setattr(ps, 'Arm', SlowEnableArm)
    port = free_port()
    log = tmp_path / 's.jsonl'
    t = threading.Thread(target=lambda: ps.main(
        ['right', '--operator-cutoff', '--port', str(port), '--log', str(log)]), daemon=True)
    t.start()
    deadline = time.monotonic() + 10
    while True:
        try:
            early = socket.create_connection(('127.0.0.1', port), timeout=10)
            break
        except ConnectionRefusedError:
            assert time.monotonic() < deadline, 'server never listened'
            time.sleep(.02)
    with early:
        early.sendall(b'{"cmd": "move", "q": [0, 0, 0, 0, 0, 0], "speed": 5}\n')
        assert 'enable' not in fake_hw.calls
        gate.set()
        assert fake_hw.serving.wait(10), 'server never started serving'
        try:
            got = early.recv(65536)
        except ConnectionResetError:
            got = b''
    assert got == b'', got
    with socket.create_connection(('127.0.0.1', port), timeout=10) as s:
        s.sendall(b'{"cmd": "release_supported"}\n')
        assert json.loads(s.makefile().readline())['release_acknowledged'] is True
    t.join(10)
    assert not t.is_alive()
    events = [json.loads(line)['event'] for line in log.read_text().splitlines()]
    assert 'move' not in events and 'refused_early_clients' in events
    assert 'REFUSED 1 connection(s) made before READY' in capsys.readouterr().out


def test_busy_port_exit_code_in_a_real_process(tmp_path):
    """Same check through `python pickup_server.py`, with the hardware classes
    replaced inside the child before main runs."""
    holder, port = busy_port()
    child = (
        "import sys; sys.path.insert(0, %r)\n"
        "import pickup_server as ps\n"
        "def boom(*a, **k): raise SystemExit('touched hardware')\n"
        "ps.GsUsbBackend = ps.Arm = ps.resolve = boom\n"
        "sys.exit(ps.main(sys.argv[1:]))\n" % str(TOOLS))
    try:
        p = subprocess.run([sys.executable, '-c', child, 'right', '--operator-cutoff', '--port', str(port),
                            '--log', str(tmp_path / 's.jsonl')],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60,
                           env=dict(os.environ, PYTHONPATH=str(TOOLS)))
    finally:
        holder.close()
    assert p.returncode == 1, (p.returncode, p.stdout, p.stderr)
    assert 'PORT_BUSY' in p.stderr
    assert 'touched hardware' not in p.stderr and 'READY' not in p.stdout
