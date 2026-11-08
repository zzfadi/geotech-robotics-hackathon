"""Diagnostics must report uncertainty and must never enter the motion path."""

import json
import math
import sys
from types import SimpleNamespace as NS

import pytest

import diagnose_arms as diagnostic


def fake_arm(position=-103.8, raw=256.2, fresh=True):
    motor = NS(feedback=NS(position=math.radians(raw), velocity=0., torque=0.,
                          temperature=31., warning_flag=False, fault_flag=False, mode=0),
               feedback_age=0.01, feedback_sequence=1, fault_status=NS(word=0),
               fault_latched=None, params_supported=False)
    joint = NS(motor_id=7, label="gripper", lo=math.radians(-96), hi=math.radians(-2.5))
    # No enable/disable/target methods: calling any motion path fails the test.
    return NS(config=NS(joints=[joint], feedback_timeout=.25), motors=[motor],
              get_joint_positions=lambda: [math.radians(position)],
              connect=lambda: None, disconnect=lambda: None, refresh=lambda: [fresh])


def test_wrap_correction_does_not_hide_outside_nominal_position():
    row = diagnostic.snapshot(fake_arm(), [True])["joints"][0]
    assert row["raw_motor_deg"] == pytest.approx(256.2)
    assert row["sdk_joint_deg"] == pytest.approx(-103.8)
    assert not row["within_nominal_limits"]
    assert not row["mode_telemetry_supported"]
    assert not row["watchdog_verified"]


@pytest.mark.parametrize("kind", ["no_reply", "stale", "nan"])
def test_invalid_feedback_is_not_good_position(kind):
    arm = fake_arm(-50)
    if kind == "stale":
        arm.motors[0].feedback_age = 1.
    if kind == "nan":
        arm.motors[0].feedback.position = float("nan")
    row = diagnostic.snapshot(arm, [kind != "no_reply"])["joints"][0]
    assert not row["fresh"]
    assert not row["within_nominal_limits"]
    json.dumps(row, allow_nan=False)


def test_missing_adapter_is_reported_and_present_arm_still_inspected(monkeypatch):
    monkeypatch.setattr(diagnostic, "load_names", lambda: {"A": "right", "B": "left"})
    monkeypatch.setattr(diagnostic, "list_adapters", lambda: [{"serial": "A"}])
    monkeypatch.setattr(diagnostic, "make_arm", lambda serial: fake_arm(-50))
    report = diagnostic.diagnose(1)
    assert report["missing_expected_adapters"] == [{"serial": "B", "name": "left"}]
    assert report["arms"][0]["samples"]
    assert not report["inspection_ok"]


def test_connection_failure_releases_transport_and_is_reported(monkeypatch):
    arm = fake_arm()
    closed = []
    def fail():
        raise RuntimeError("no motor replies")
    arm.connect = fail
    arm.disconnect = lambda: closed.append(True)
    monkeypatch.setattr(diagnostic, "load_names", lambda: {"A": "right"})
    monkeypatch.setattr(diagnostic, "list_adapters", lambda: [{"serial": "A"}])
    monkeypatch.setattr(diagnostic, "make_arm", lambda serial: arm)
    report = diagnostic.diagnose(1)
    assert closed == [True]
    assert "no motor replies" in report["arms"][0]["error"]
    assert not report["inspection_ok"]


@pytest.mark.parametrize("arg", ["--help", "--samples=0", "--samples=101"])
def test_argument_handling_before_usb_access(arg, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["diagnose", arg])
    def forbidden():
        raise AssertionError("must not discover USB devices")
    monkeypatch.setattr(diagnostic, "list_adapters", forbidden)
    with pytest.raises(SystemExit) as result:
        diagnostic.main()
    assert result.value.code == (0 if arg == "--help" else 2)
