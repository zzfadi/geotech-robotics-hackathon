import math
from types import SimpleNamespace

import pytest

from claw_probe import check_feedback, claw_config, move_to, plan_open_target, plan_targets, run_probe
from maker_arm.arm import ArmState
from maker_arm import protocol


def test_isolates_gripper_and_keeps_watchdog():
    config = claw_config()
    assert config.required_motor_ids == [7]
    assert [j.motor_id for j in config.joints] == [7]
    assert config.gravity_scale == 0
    assert not config.hold_on_fault
    assert config.joints[0].can_timeout_ms == 500
    assert config.joints[0].release_on_fault
    assert config.max_velocity == pytest.approx(math.radians(3))


@pytest.mark.parametrize("degrees", [-12, -7, -2.3, -0.1, 0])
def test_targets_stay_small_and_inside_vendor_bounds(degrees):
    joint = claw_config().joints[0]
    start = math.radians(degrees)
    opened, closed = plan_targets(start, joint)
    assert joint.lo <= opened < closed <= joint.hi
    assert 0 < start - opened <= math.radians(5) + 1e-12
    assert closed <= start


@pytest.mark.parametrize("degrees", [math.nan, math.inf, -math.inf, 1, -30])
def test_rejects_unexpected_initial_pose(degrees):
    with pytest.raises(ValueError):
        plan_targets(math.radians(degrees), claw_config().joints[0])


@pytest.mark.parametrize("degrees", [-96, -94, -22, -6, 0])
def test_full_open_can_start_from_partially_open_position(degrees):
    joint = claw_config(full_open=True).joints[0]
    assert plan_open_target(math.radians(degrees), joint) == joint.lo


@pytest.mark.parametrize("degrees", [math.nan, math.inf, -97, 1])
def test_full_open_rejects_unexpected_pose(degrees):
    with pytest.raises(ValueError):
        plan_open_target(math.radians(degrees), claw_config(full_open=True).joints[0])


def fake_arm():
    fb = SimpleNamespace(position=math.radians(-3), velocity=0, torque=0,
                         temperature=25, mode=protocol.MODE_RESET,
                         warning_flag=False, fault_flag=False)
    motor = SimpleNamespace(feedback=fb, feedback_age=0, feedback_sequence=1,
                            fault_latched=None, params_supported=True,
                            read_param=lambda *a: 0)
    events = []
    arm = SimpleNamespace(
        motors=[motor], config=claw_config(), state=ArmState.CONNECTED,
        fault_reason=None, unconfirmed_disable=[],
        connect=lambda: events.append("connect"),
        refresh=lambda: [True],
        get_joint_positions=lambda: [fb.position],
        enable=lambda: events.append("enable"),
        disable=lambda: events.append("disable"),
        disconnect=lambda: events.append("disconnect"),
    )
    return arm, events


def test_read_only_default_never_enables_or_disables(monkeypatch):
    monkeypatch.setattr("claw_probe.time.sleep", lambda _: None)
    arm, events = fake_arm()
    result = run_probe(arm)
    assert not result["motion_requested"]
    assert events == ["connect", "disconnect"]


def test_missing_watchdog_blocks_enable():
    arm, events = fake_arm()
    arm.motors[0].params_supported = False
    with pytest.raises(RuntimeError, match="watchdog"):
        run_probe(arm, move=True)
    assert events == ["connect", "disconnect"]


def test_manual_cutoff_is_explicit_and_never_reported_as_watchdog(monkeypatch):
    monkeypatch.setattr("claw_probe.time.sleep", lambda _: None)
    monkeypatch.setattr("claw_probe.move_to", lambda arm, goal, *args, **kwargs: goal)
    arm, events = fake_arm()
    arm.motors[0].params_supported = False

    def unexpected_parameter_access(*args):
        raise AssertionError("Do not use unsupported parameter access")

    arm.motors[0].read_param = unexpected_parameter_access
    result = run_probe(arm, move=True, operator_cutoff=True)
    assert result["watchdog_ms"] is None
    assert result["original_watchdog_ticks"] is None
    assert result["operator_cutoff"]
    assert events == ["connect", "enable", "disable", "disconnect"]


def test_legacy_mode_zero_is_not_treated_as_verified_disabled():
    arm, _ = fake_arm()
    arm.state = ArmState.ENABLED
    arm.motors[0].params_supported = False
    assert check_feedback(arm, math.radians(-3), math.radians(-8)) == math.radians(-3)


def test_enable_failure_still_releases(monkeypatch):
    monkeypatch.setattr("claw_probe.time.sleep", lambda _: None)
    arm, events = fake_arm()

    def fail_enable():
        events.append("enable")
        raise RuntimeError("enable reply lost")

    arm.enable = fail_enable
    with pytest.raises(RuntimeError, match="enable reply lost"):
        run_probe(arm, move=True)
    assert events == ["connect", "enable", "disable", "disconnect"]


def test_partial_motion_keeps_evidence_and_releases(monkeypatch):
    monkeypatch.setattr("claw_probe.time.sleep", lambda _: None)
    arm, events = fake_arm()
    report = {}

    def fail_move(arm, goal, start, opened, samples, full_open=False):
        samples.append({"position_deg": -4})
        raise RuntimeError("target not reached")

    monkeypatch.setattr("claw_probe.move_to", fail_move)
    with pytest.raises(RuntimeError, match="target not reached"):
        run_probe(arm, move=True, report=report)
    assert not report["motion_completed"]
    assert report["stage"] == "opening"
    assert report["samples"] == [{"position_deg": -4}]
    assert report["release_command_acknowledged"]
    assert events == ["connect", "enable", "disable", "disconnect"]


def test_full_open_only_commands_open_endpoint_and_never_closes(monkeypatch):
    monkeypatch.setattr("claw_probe.time.sleep", lambda _: None)
    goals = []

    def reach(arm, goal, *args, **kwargs):
        goals.append((goal, kwargs.get("full_open", False)))
        return goal

    monkeypatch.setattr("claw_probe.move_to", reach)
    arm, events = fake_arm()
    arm.config = claw_config(full_open=True)
    result = run_probe(arm, move=True, open_only=True)
    assert goals == [(arm.config.joints[0].lo, True)]
    assert result["close_target_deg"] is None
    assert result["motion_completed"]
    assert arm.config.joints[0].kp == 10
    assert arm.config.joints[0].kd == 2
    assert arm.config.joints[0].hold_torque_nm is None
    assert arm.config.max_velocity == pytest.approx(math.radians(3))
    assert events == ["connect", "enable", "disable", "disconnect"]


def test_full_open_stall_never_accumulates_large_following_error(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("claw_probe.time.monotonic", lambda: clock[0])

    def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr("claw_probe.time.sleep", sleep)
    arm, _ = fake_arm()
    arm.config = claw_config(full_open=True)
    arm.state = ArmState.ENABLED
    arm.motors[0].feedback.mode = protocol.MODE_RUN
    goals = []

    def set_target(target):
        goals.append(target[0])
        return True

    arm.set_joint_targets = set_target
    start = arm.get_joint_positions()[0]
    goal = arm.config.joints[0].lo
    with pytest.raises(RuntimeError, match="stopped progressing"):
        move_to(arm, goal, start, goal, [], full_open=True)
    assert goals
    assert all(0 <= start - target <= math.radians(2.5) + 1e-12 for target in goals)


@pytest.mark.parametrize("field,value", [
    ("feedback_age", 0.2),
    ("fault_latched", "fault"),
    ("warning_flag", True),
    ("torque", 0.7),
    ("velocity", math.radians(25)),
    ("position", math.nan),
    ("temperature", 45),
    ("mode", protocol.MODE_RESET),
])
def test_bad_feedback_stops(field, value):
    arm, _ = fake_arm()
    arm.state = ArmState.ENABLED
    arm.motors[0].feedback.mode = protocol.MODE_RUN
    target = arm.motors[0] if field in ("feedback_age", "fault_latched") else arm.motors[0].feedback
    setattr(target, field, value)
    with pytest.raises(RuntimeError):
        check_feedback(arm, math.radians(-3), math.radians(-8))
