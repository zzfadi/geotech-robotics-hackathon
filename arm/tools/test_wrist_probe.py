import math
from types import SimpleNamespace as NS
import pytest
import wrist_probe as w
from maker_arm.arm import ArmState


def arm():
    cfg = w.wrist_config()
    fb = NS(position=0.,velocity=0.,torque=0.,temperature=30.,warning_flag=False,fault_flag=False,mode=0)
    m = NS(feedback=fb,feedback_age=.01,fault_latched=None,params_supported=False,feedback_sequence=1)
    a = NS(config=cfg,motors=[m],state=ArmState.CONNECTED,fault_reason=None,
           get_joint_positions=lambda: [0.], refresh=lambda: [True], unconfirmed_disable=[])
    a.calls=[]
    a.connect=lambda: a.calls.append('connect')
    a.disconnect=lambda: a.calls.append('disconnect')
    def enable():
        a.calls.append('enable'); a.state=ArmState.ENABLED
    def disable():
        a.calls.append('disable'); a.state=ArmState.CONNECTED
    a.enable=enable; a.disable=disable
    return a


def test_only_motor6_with_strict_limits_and_slow_low_gain_control():
    c=w.wrist_config()
    assert c.required_motor_ids == [6]
    assert [j.motor_id for j in c.joints] == [6]
    assert c.gravity_scale == 0 and not c.hold_on_fault and c.full_turn_grace == 0
    assert c.max_velocity == pytest.approx(math.radians(1))
    assert c.joints[0].kp == 5 and c.joints[0].kd == 1


def test_vendor_gains_preserve_speed_and_motor_scope():
    c=w.wrist_config(True)
    assert c.joints[0].kp == 30 and c.joints[0].kd == 2
    assert c.required_motor_ids == [6]
    assert c.max_velocity == pytest.approx(math.radians(1))
    assert c.feedback_timeout == .15 and c.full_turn_grace == 0


@pytest.mark.parametrize('field,value', [('feedback_age',.2),('feedback_age',float('nan')),('temperature',45),('torque',.41),
    ('velocity',math.radians(16)),('warning_flag',True),('position',float('nan'))])
def test_feedback_guards(field,value):
    a=arm(); a.state=ArmState.ENABLED
    setattr(a.motors[0] if field=='feedback_age' else a.motors[0].feedback,field,value)
    with pytest.raises(RuntimeError): w.validate(a,0.,True)


def test_envelope_guard():
    a=arm(); a.state=ArmState.ENABLED
    a.get_joint_positions=lambda: [math.radians(4)]
    with pytest.raises(RuntimeError,match='envelope'): w.validate(a,0.,True)


def test_no_move_default_and_cutoff_required(monkeypatch):
    monkeypatch.setattr(w.time,'sleep',lambda _:None)
    a=arm(); r={}; w.run(a,r)
    assert a.calls == ['connect','disconnect']
    assert r['stage']=='inspection_only'
    a=arm()
    with pytest.raises(RuntimeError,match='cutoff'): w.run(a,{},move=True)
    assert a.calls == ['connect','disconnect']


def test_success_returns_before_disabling(monkeypatch):
    monkeypatch.setattr(w.time,'sleep',lambda _:None)
    a=arm(); targets=[]
    def segment(a,target,start,report):
        targets.append(target); return math.degrees(target)
    monkeypatch.setattr(w,'segment',segment)
    r={}; w.run(a,r,True,True)
    assert targets == [w.STEP,0.]
    assert a.calls == ['connect','enable','disable','disconnect']
    assert r['motion_completed'] and r['release_command_acknowledged']
    assert not r['release_mode_verified']


@pytest.mark.parametrize('stage',['enable','outbound'])
def test_failure_releases_and_does_not_attempt_another_segment(stage,monkeypatch):
    monkeypatch.setattr(w.time,'sleep',lambda _:None)
    a=arm()
    def fail(*args): raise RuntimeError('injected failure')
    if stage=='enable': a.enable=fail
    else: monkeypatch.setattr(w,'segment',fail)
    r={}
    with pytest.raises(RuntimeError,match='injected'): w.run(a,r,True,True)
    assert a.calls[-2:] == ['disable','disconnect']
    assert not r['motion_completed']


def test_segment_deadline_and_bounded_following_error(monkeypatch):
    a=arm(); a.state=ArmState.ENABLED
    targets=[]; a.set_joint_targets=lambda t: targets.append(t) or True
    clock=iter([0.,0.,0.,9.])
    monkeypatch.setattr(w.time,'monotonic',lambda:next(clock))
    monkeypatch.setattr(w.time,'sleep',lambda _:None)
    with pytest.raises(RuntimeError,match='8.0 seconds'):
        w.segment(a,w.STEP,0.,{'_began':0.,'samples':[],'stage':'outbound'})
    assert targets == [[pytest.approx(math.radians(.5))]]


@pytest.mark.parametrize('value',[0.,-1.,11.,float('inf'),float('nan')])
def test_step_hard_bounds_before_connect(value):
    a=arm()
    with pytest.raises(ValueError): w.run(a,{},step_deg=value)
    assert not a.calls


@pytest.mark.parametrize('value',[0.,.5,-1.,6.,float('inf'),float('nan')])
def test_speed_hard_bounds(value):
    with pytest.raises(ValueError): w.wrist_config(True,value)


def test_bad_preflight_preserves_serializable_evidence(monkeypatch):
    import json
    a=arm(); a.motors[0].feedback.temperature=float('nan')
    r={}
    with pytest.raises(RuntimeError,match='Nonfinite'): w.run(a,r)
    assert r['preflight_samples'][0]['temperature_c'] is None
    json.dumps(r,allow_nan=False)
    assert a.calls == ['connect','disconnect']


def test_existing_evidence_refuses_before_usb(tmp_path,monkeypatch):
    import sys
    p=tmp_path/'evidence.json'; p.write_text('previous run')
    monkeypatch.setattr(sys,'argv',['wrist_probe','right','--move','--output',str(p)])
    def forbidden(*args): raise AssertionError('must not access hardware')
    monkeypatch.setattr(w,'resolve',forbidden)
    with pytest.raises(SystemExit) as e: w.main()
    assert e.value.code == 2
    assert p.read_text() == 'previous run'
