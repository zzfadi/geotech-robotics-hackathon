"""Offline packaging checks. USB discovery and backend opens are forbidden."""

import sys
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import can
import pytest

import arms
import claw_probe
import gsusb_backend
import scan_arms
import watch_arms
import wrist_probe
import diagnose_arms


@pytest.fixture(autouse=True)
def forbid_hardware(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline tests must not access USB/CAN")

    monkeypatch.setattr(gsusb_backend.GsUsb, "scan", forbidden)
    monkeypatch.setattr(gsusb_backend.GsUsbBackend, "open", forbidden)


@pytest.mark.parametrize("module", [scan_arms, watch_arms, claw_probe, wrist_probe, diagnose_arms])
def test_help_exits_before_hardware(module, monkeypatch):
    monkeypatch.setattr(sys, "argv", [module.__name__, "--help"])
    with pytest.raises(SystemExit) as result:
        module.main()
    assert result.value.code == 0


@pytest.mark.parametrize("seconds", ["0", "-1", "nan", "inf"])
def test_watch_invalid_duration_exits_before_hardware(seconds, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["watch", f"--seconds={seconds}"])
    with pytest.raises(SystemExit) as result:
        watch_arms.main()
    assert result.value.code == 2


def test_claw_missing_argument_exits_before_hardware(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["claw_probe"])
    with pytest.raises(SystemExit) as result:
        claw_probe.main()
    assert result.value.code == 2


def test_names_match_tested_rig():
    assert arms.load_names() == {
        "2077378045465008": "left",
        "0027002E4146570520313238": "right",
    }


@pytest.mark.parametrize("selector", ["left", "111ABC", "ABC"])
def test_unique_selector(selector, monkeypatch):
    monkeypatch.setattr(arms, "adapters_by_name", lambda: {"left": "111ABC", "right": "222DEF"})
    assert arms.resolve(selector) == ("left", "111ABC")


@pytest.mark.parametrize("selector", ["ABC", "", "missing"])
def test_ambiguous_or_missing_selector_refuses(selector, monkeypatch):
    monkeypatch.setattr(arms, "adapters_by_name", lambda: {"left": "111ABC", "right": "222ABC"})
    with pytest.raises(SystemExit):
        arms.resolve(selector)


def test_duplicate_names_refuse(monkeypatch):
    monkeypatch.setattr(arms, "load_names", lambda: {"111ABC": "same", "222ABC": "same"})
    monkeypatch.setattr(arms, "list_adapters", lambda: [{"serial": "111ABC"}, {"serial": "222ABC"}])
    with pytest.raises(SystemExit, match="Duplicate"):
        arms.adapters_by_name()


def test_rx_filters_echo_error_and_remote_without_hardware():
    backend = gsusb_backend.GsUsbBackend("mock")
    messages = iter([
        can.Message(arbitration_id=7, data=b"echo", is_rx=False),
        can.Message(arbitration_id=7, is_error_frame=True),
        can.Message(arbitration_id=7, is_remote_frame=True),
        can.Message(arbitration_id=7, data=b"reply", is_extended_id=False),
    ])
    backend._bus = SimpleNamespace(recv=lambda timeout: next(messages))
    backend._running = True
    received = []

    def receive(can_id, data):
        received.append(data)
        backend._running = False

    backend.set_recv_callback(receive)
    backend._rx_loop()
    assert received == [b"reply"]


def test_no_machine_paths_in_portable_tools():
    for path in Path(__file__).parent.iterdir():
        if path.suffix == ".py" and path != Path(__file__):
            assert "/Users/" not in path.read_text()
            assert "/opt/homebrew/" not in path.read_text()


def test_launcher_preserves_caller_directory_for_output_paths(tmp_path):
    fake_python = tmp_path / "fake-python"
    fake_python.write_text("#!/bin/sh\npwd\n")
    fake_python.chmod(0o755)
    launcher = Path(__file__).with_name("arm").resolve()
    result = subprocess.run(["bash", str(launcher), "diagnose"], cwd=tmp_path,
        env={**os.environ, "ARMCTL_PYTHON": str(fake_python)},
        capture_output=True, text=True, check=True)
    assert Path(result.stdout.strip()) == tmp_path.resolve()
