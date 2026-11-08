import math
import os
import time
from types import SimpleNamespace as NS

import pytest
import lift_probe as lift
from maker_arm.arm import ArmState


def arm():
    cfg = lift.lift_config()
    q = list(map(math.radians, [2., 0., 11., -1., 10., 1.5]))
    motors = [NS(feedback=NS(velocity=0., torque=0., temperature=30.,
                 warning_flag=False, fault_flag=False, mode=0), feedback_age=.01,
                 fault_latched=None, params_supported=False) for _ in range(6)]
    a = NS(config=cfg, motors=motors, state=ArmState.CONNECTED, fault_reason=None,
           get_joint_positions=lambda: q, unconfirmed_disable=[], calls=[])
    a.disconnect = lambda: a.calls.append('disconnect')
    return a


def test_profile_excludes_gripper_and_preserves_hold_and_gravity():
    cfg = lift.lift_config()
    assert cfg.required_motor_ids == [1, 2, 3, 4, 5, 6]
    assert cfg.hold_on_fault and cfg.gravity_scale == 1.
    assert cfg.max_velocity == pytest.approx(math.radians(3))
    assert cfg.rest_speed == cfg.max_velocity
    assert all(j.return_to_rest and not j.release_on_fault for j in cfg.joints)


def test_folded_shoulder_is_clipped_without_changing_limits():
    a = arm()
    rest, target = lift.make_targets(a.config, a.get_joint_positions(), 15)
    assert math.degrees(rest[1]) == pytest.approx(-3.2)
    assert math.degrees(target[2]) == pytest.approx(26.)
    assert target[:2] == rest[:2] and target[3:] == rest[3:]


@pytest.mark.parametrize('step', [0, -1, 31, float('nan'), float('inf')])
def test_bad_steps_rejected(step):
    a = arm()
    with pytest.raises(ValueError):
        lift.make_targets(a.config, a.get_joint_positions(), step)


@pytest.mark.parametrize('index,value', [(1, 1.), (0, 160.), (2, -1.)])
def test_unrecognized_start_pose_rejected(index, value):
    a = arm(); q = a.get_joint_positions().copy(); q[index] = math.radians(value)
    with pytest.raises(RuntimeError):
        lift.make_targets(a.config, q, 15.)


@pytest.mark.parametrize('field,value', [('feedback_age', .3), ('feedback_age', float('nan')),
    ('velocity', math.radians(21)), ('torque', 6.1), ('temperature', 42.),
    ('warning_flag', True), ('fault_flag', True), ('velocity', float('nan'))])
def test_feedback_failure_halts_segment(field, value):
    a = arm(); m = a.motors[2]
    setattr(m if field == 'feedback_age' else m.feedback, field, value)
    with pytest.raises(RuntimeError):
        lift.validate(a)


def test_envelope_rejects_other_joint_motion():
    a = arm(); start = a.get_joint_positions().copy()
    _, target = lift.make_targets(a.config, start, 15.)
    a.get_joint_positions()[0] += math.radians(5)
    with pytest.raises(RuntimeError, match='envelope'):
        lift.validate(a, start, target)


def test_failed_return_never_disconnects():
    a = arm(); a.state = ArmState.ENABLED; a.stop = lambda: a.calls.append('stop') or False
    report = {}
    assert lift.finish(a, report) is False
    assert a.calls == ['stop'] and not report['return_verified']


def test_unacknowledged_release_never_disconnects():
    a = arm(); a.stop = lambda: True; a.unconfirmed_disable = [3]
    assert lift.finish(a, {}) is False
    assert a.calls == []


def test_verified_return_and_release_disconnect():
    a = arm(); a.state = ArmState.ENABLED; a.stop = lambda: True
    report = {}
    assert lift.finish(a, report) is True
    assert a.calls == ['disconnect']
    assert report['release_command_acknowledged'] and not report['release_mode_verified']


def test_startup_failure_does_not_claim_physical_return():
    a = arm(); a.stop = lambda: True
    report = {}
    assert lift.finish(a, report)
    assert report['return_verified'] is None
    assert report['stop_from_state'] == 'not_enabled'


def test_capture_marker_without_new_images_does_not_authorize_motion(tmp_path):
    (tmp_path / 'capture-ready.txt').write_text('{}')
    with pytest.raises(RuntimeError, match='no saved frames'):
        lift.camera_guard(tmp_path)
    frame = tmp_path / 'camera-overview.001.jpg'; frame.write_bytes(b'test')
    assert lift.camera_guard(tmp_path)['image'] == str(frame)
    past = time.time() - 2
    os.utime(frame, (past, past))
    with pytest.raises(RuntimeError, match='fresh frames'):
        lift.camera_guard(tmp_path)


def test_camera_loss_interrupts_before_sending_another_target(monkeypatch, tmp_path):
    a = arm(); a.state = ArmState.ENABLED
    start = a.get_joint_positions().copy()
    _, target = lift.make_targets(a.config, start, 15.)
    a.set_joint_targets = lambda _: pytest.fail('No new targets after camera loss')
    with pytest.raises(RuntimeError, match='running overview'):
        lift.segment(a, target, start, target, {'stage': 'lift', 'samples': []},
                     camera_directory=tmp_path)


def test_evidence_write_failure_cannot_escape_hold_path(tmp_path):
    assert lift.save({'position': float('nan')}, tmp_path / 'report.json')
    assert 'null' in (tmp_path / 'report.json').read_text()
    assert lift.save({}, tmp_path / 'missing' / 'report.json') is False


def test_segment_bounds_demand_and_stops_without_release_on_timeout(monkeypatch):
    a = arm(); a.state = ArmState.ENABLED
    start = a.get_joint_positions().copy()
    _, goal = lift.make_targets(a.config, start, 15.)
    demands = []; a.set_joint_targets = lambda q: demands.append(q) or True
    ticks = iter([0., 0., 0., 26.])
    monkeypatch.setattr(lift.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(lift.time, 'sleep', lambda _: None)
    with pytest.raises(RuntimeError, match='converge'):
        lift.segment(a, goal, start, goal, {'stage': 'lift', 'samples': []})
    assert math.degrees(demands[0][2] - start[2]) == pytest.approx(2)
    assert a.calls == []
