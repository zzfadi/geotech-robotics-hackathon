"""Supervised bounded encoder/image collection, not autonomous pickup control."""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import time

import cv2
import numpy as np

from arms import resolve
from gsusb_backend import GsUsbBackend
from maker_arm.arm import Arm
import lift_probe as lift
from calibration_geometry import PlanarFeatureTracker, IncrementalPlanarTracker, raw_motor_fk


class CameraSequence:
    def __init__(self, directory, first_image, initial_marker):
        self.directory=directory
        self.last=Path(first_image).name
        # Use the logo plane, excluding the housing rim and protruding connector.
        outer=np.asarray(initial_marker['corners_px'],np.float32)
        H=cv2.getPerspectiveTransform(np.float32([[0,0],[1,0],[1,1],[0,1]]),outer)
        inner=cv2.perspectiveTransform(np.float32([[[.15,.13],[.87,.13],[.87,.62],[.15,.62]]]),H)[0]
        self.tracker=IncrementalPlanarTracker(cv2.imread(first_image),inner)

    def detect_frame(self, filename):
        target=Path(filename).name
        files=sorted(p for p in self.directory.glob('camera-*.jpg') if self.last < p.name <= target)
        if not files:raise RuntimeError('No new image sequence for marker tracking')
        for path in files:
            result=self.tracker.update(cv2.imread(str(path)))
            self.last=path.name
        return result


def pose_plan(config, start):
    rest, _ = lift.make_targets(config, start, 30.)
    # Degrees relative to measured rest: pan, shoulder, elbow lift, wrist roll.
    # Higher elbow poses hide the natural marker. Keep it visible and vary axes.
    offsets = [(0,0,10,0),(0,0,15,0),(0,0,15,8),(0,0,15,-8),
               (8,-5,15,8),(-8,-5,15,-8),(8,-8,15,0),(-8,-8,15,0),
               (-4,-4,12,4),(4,-4,12,-4)]
    poses = []
    for index, (pan, shoulder, elbow, roll) in enumerate(offsets):
        q = list(rest)
        for axis, angle in [(0,pan),(1,shoulder),(2,elbow),(5,roll)]:
            q[axis] += math.radians(angle)
        if any(not j.lo <= p <= j.hi for p,j in zip(q,config.joints)):
            raise ValueError('Calibration pose outside original SDK joint limits')
        poses.append({'label':f'pose_{index:02d}', 'role':'validation' if index >= 8 else 'fit', 'q':q})
    return rest, poses


def capture_pose(arm, camera, tracker, roi, low, high, report):
    deadline = time.monotonic()+5
    window = []
    while time.monotonic() < deadline:
        lift.validate(arm, low, high, enabled=True)
        lift.camera_guard(camera)
        window.append(lift.snapshot(arm,report['stage']))
        window = [s for s in window if s['monotonic_s'] >= time.monotonic()-.7]
        if len(window) >= 10 and window[-1]['monotonic_s']-window[0]['monotonic_s'] >= .5:
            values = np.array([s['position_deg'] for s in window])
            if np.max(np.ptp(values,axis=0)) <= .15:
                frame = json.loads((camera/'capture-latest.json').read_text())
                age = time.monotonic()-frame['receipt_uptime_s']
                if not 0 <= age <= .4 or frame['receipt_uptime_s'] < window[0]['monotonic_s']:
                    raise RuntimeError('No timestamp-aligned camera frame in stable pose window')
                card = tracker.detect_frame(frame['image'])
                degrees = values.mean(axis=0).tolist()
                return {'position_deg': degrees, 'position_span_deg':np.ptp(values,axis=0).tolist(),
                        'joint_window_monotonic_s':[window[0]['monotonic_s'],window[-1]['monotonic_s']],
                        'camera':frame, 'marker':card, 'T_base_wrist_sdk':raw_motor_fk(degrees).tolist()}
        time.sleep(.05)
    raise RuntimeError('Arm did not provide a stationary calibration image window')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('arm');p.add_argument('--move',action='store_true')
    p.add_argument('--operator-cutoff',action='store_true')
    p.add_argument('--camera-directory',type=Path,required=True)
    p.add_argument('--template',type=Path,required=True)
    p.add_argument('--roi',type=int,nargs=4,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.move and (not a.operator_cutoff or not sys.stdin.isatty()):
        p.error('Powered sweep requires operator cutoff and an interactive terminal')
    if a.output.exists():p.error('Use a new report file')
    config=lift.lift_config()
    if any(j.direction != 1 or j.offset != 0 for j in config.joints):
        p.error('Raw-motor FK requires the pinned unmodified coordinate profile')
    tracker=PlanarFeatureTracker(a.template)
    name,serial=resolve(a.arm)
    arm=Arm(config,GsUsbBackend(serial)); attempted=False
    report={'when':datetime.now(timezone.utc).isoformat(),'arm':name,'serial':serial,
            'motion_requested':a.move,'motion_completed':False,'samples':[], 'observations':[],
            'gripper_enabled':False, 'watchdog_verified':False, 'camera_roi':a.roi,
            'reference_kind':'natural_wrist_texture', 'marker_size_mm':None,
            'separate_table_reference':{'card_size_mm':[63,88], 'operator_confirmed':True}}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    try:
        arm.connect()
        start,_,_=lift.preflight(arm,report,30.)
        rest,poses=pose_plan(config,start)
        report['planned_poses']=[{'label':s['label'],'role':s['role'],'position_deg':list(map(math.degrees,s['q']))} for s in poses]
        points=np.array([start,rest]+[s['q'] for s in poses])
        low,high=points.min(axis=0).tolist(),points.max(axis=0).tolist()
        frame=lift.camera_guard(a.camera_directory)
        report['initial_marker']=tracker.detect(cv2.imread(frame['image']),a.roi)
        tracker=CameraSequence(a.camera_directory,frame['image'],report['initial_marker'])
        if not lift.save(report,a.output):raise RuntimeError('Evidence output unavailable')
        if not a.move:
            report['stage']='inspection_only';arm.disconnect()
            return 0
        attempted=True;report['stage']='enable';arm.enable()
        for pose in poses:
            report['stage']=pose['label'];print(pose['label'],pose['role'],flush=True)
            lift.segment(arm,pose['q'],low,high,report,camera_directory=a.camera_directory)
            observation=capture_pose(arm,a.camera_directory,tracker,a.roi,low,high,report)
            observation.update(label=pose['label'],role=pose['role'])
            report['observations'].append(observation)
            if not lift.save(report,a.output):raise RuntimeError('Evidence write failed; stop collection')
            print('OBSERVED',pose['label'],observation['marker']['inliers'],flush=True)
        report['stage']='return'
        lift.segment(arm,rest,low,high,report,camera_directory=a.camera_directory)
        if not lift.finish(arm,report):raise RuntimeError('Rest or release unverified')
        report.update(stage='completed',motion_completed=True)
    except BaseException as exc:
        report['error']=f'{type(exc).__name__}: {exc}';print(report['error'],flush=True)
        if attempted:lift.keep_holding(arm,report,a.output)
        else:arm.disconnect()
    finally:
        lift.save(report,a.output)
        print(json.dumps({k:v for k,v in report.items() if k not in ['samples','preflight_samples','observations','planned_poses']}),flush=True)
    return 1 if 'error' in report else 0


if __name__=='__main__':sys.exit(main())
