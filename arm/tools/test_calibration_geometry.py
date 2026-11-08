import math
import cv2
import numpy as np
import pytest

from calibration_geometry import raw_motor_fk,PlanarFeatureTracker
from calibration_sweep import pose_plan
from lift_probe import lift_config


def test_fk_rigid_transform_and_observed_lift_direction():
    q=[2,-4,10,-1,10,1.5]
    a=raw_motor_fk(q)
    b=raw_motor_fk([2,-4,40,-1,10,1.5])
    np.testing.assert_allclose(a[:3,:3].T@a[:3,:3],np.eye(3),atol=1e-12)
    assert np.linalg.det(a[:3,:3]) == pytest.approx(1)
    grasp=np.array([0,0,-.169638704618,1])
    assert (b@grasp)[2] > (a@grasp)[2]+.1


def test_pose_plan_bounded_and_validation_reserved():
    q=np.radians([2,-4,10,-1,10,1.5]).tolist()
    rest,poses=pose_plan(lift_config(),q)
    assert len(poses)==10 and [p['role'] for p in poses].count('validation')==2
    delta=np.degrees(np.array([p['q'] for p in poses])-q)
    assert max(abs(delta[:,0]))<=8.00001 and max(abs(delta[:,5]))<=8.00001
    assert min(delta[:,2])>=9.99999 and max(delta[:,2])<=15.00001
    assert min(delta[:,1])>=-8.00001 and max(delta[:,1])<=.00001
    np.testing.assert_allclose(delta[:,[3,4]],0,atol=1e-10)
    assert rest==q


def test_invalid_fk_and_pose_inputs_rejected():
    with pytest.raises(ValueError):raw_motor_fk([0]*5)
    with pytest.raises(ValueError):raw_motor_fk([float('nan')]*6)
    with pytest.raises(ValueError):pose_plan(lift_config(),np.radians([155,-4,10,0,0,150]).tolist())


def test_feature_tracking_and_blank_rejection(tmp_path):
    rng=np.random.default_rng(42)
    template=rng.integers(0,256,(352,252),dtype=np.uint8)
    template=cv2.GaussianBlur(template,(3,3),.5)
    path=tmp_path/'template.png';cv2.imwrite(str(path),template)
    tracker=PlanarFeatureTracker(path)
    image=np.full((600,800),100,dtype=np.uint8)
    image[100:452,200:452]=template
    found=tracker.detect(image)
    np.testing.assert_allclose(found['corners_px'],[[200,100],[452,100],[452,452],[200,452]],atol=1.)
    with pytest.raises(RuntimeError):tracker.detect(np.full((600,800),100,dtype=np.uint8))
