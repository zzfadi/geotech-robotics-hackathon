"""Offline demonstration checks/export. No DreamClient, network or actuation."""
import argparse
import json
import math
from pathlib import Path
import subprocess
import shutil

ROLES = ('overhead', 'wrist_l', 'wrist_r')
FPS = 5
MAX_SKEW = .02
MAX_PHASE_ERROR = .025
SPANS = {side + suffix: [offset + a, offset + b]
         for side, offset in [('left', 0), ('right', 16)]
         for suffix, a, b in [('_arm', 0, 6), ('_grip', 6, 7), ('_eef_xyz', 7, 10), ('_eef_rot6', 10, 16)]}
EMBODIMENT = {'state': SPANS, 'action': SPANS,
              'video': {r: 'observation.images.' + r for r in ROLES}, 'head_camera': 'overhead'}


def finite(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def vector(values):
    if not isinstance(values, list) or len(values) != 32 or not all(map(finite, values)):
        raise ValueError('Expected finite 32D joint/EEF vector')
    for offset in (0, 16):
        if not 0 <= values[offset + 6] <= 1: raise ValueError('Grip outside [0,1]')
        a, b = values[offset + 10:offset + 13], values[offset + 13:offset + 16]
        if abs(sum(x*x for x in a)-1) > .001 or abs(sum(x*x for x in b)-1) > .001 or abs(sum(x*y for x,y in zip(a,b))) > .001:
            raise ValueError('Rotation columns must be orthonormal')


def encode_state(raw):
    """Pinned CAD FK, same mounted-base/TCP convention as current simulator IK."""
    import sys
    tools=Path(__file__).resolve().parents[1]/'Unreal/TheHumanTouch/Tools'
    if str(tools) not in sys.path: sys.path.insert(0,str(tools))
    from compiled_fk import ArmFK
    fk=ArmFK(); out=[]
    for side in ('left','right'):
        q=raw['q_'+side]; gap=raw['gap_'+side+'_m']
        if not finite(gap) or not 0 <= gap <= .104825: raise ValueError('Invalid actual jaw gap')
        pose=fk.pose(q); rot=pose[:3,:3] @ fk.tool_axes
        out.extend(q);out.append(gap/.104825);out.extend(pose[:3,3].tolist())
        out.extend(rot[:,0].tolist()+rot[:,1].tolist())
    return out


def validate_samples(samples, root):
    """Reject cadence gaps; never fill missing images or synthesize observations."""
    if len(samples) < 9: raise ValueError('5 Hz needs at least nine samples')
    root = Path(root).resolve(); start = samples[0]['measured_unix']; seen = set()
    for i, s in enumerate(samples):
        if s['index'] != i: raise ValueError('Non-contiguous sample index')
        vector(s['state']); vector(s['action'])
        if s.get('state_verified') is not True or s.get('action_applied') is not True:
            raise ValueError('State/actual applied action not verified')
        if s.get('scope') != 'simulation': raise ValueError('Only explicit simulation episodes supported')
        times = [s['measured_unix'], *s['camera_unix'].values()]
        if not finite(s.get('action_unix')) or not 0 < s['action_unix']-s['measured_unix'] <= 1/FPS+MAX_PHASE_ERROR:
            raise ValueError('Action must be an acknowledged future target before the next observation')
        if set(s['camera_unix']) != set(ROLES) or not all(map(finite,times)) or max(times)-min(times) > MAX_SKEW:
            raise ValueError('Incomplete or unsynchronized sample')
        if abs(s['measured_unix']-start-i/FPS) > MAX_PHASE_ERROR:
            raise ValueError('Missing/late sample: cannot fabricate fixed-rate trajectory')
        for role in ROLES:
            p = (root/s['images'][role]).resolve()
            if not p.is_relative_to(root) or p in seen: raise ValueError('Reused/outside image path')
            seen.add(p)
            data=p.read_bytes()
            if not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'): raise ValueError('Incomplete JPEG')
    return True


def positive_eligibility(samples, review):
    """Conservative dataset gate, never inferred from numeric center-height alone."""
    if not review.get('reviewer') or review.get('grasp_and_no_external_support_verified') is not True:
        return False
    since = None
    for s in samples:
        e=s.get('cup_evidence', {}); t=s['measured_unix']
        good=(e.get('physics_simulated') is True and e.get('gravity_enabled') is True
              and 'attached_to' in e and e['attached_to'] is None
              and e.get('support_snapshot_complete') is True
              and e.get('bilateral_jaw_contacts') is True and e.get('other_support_contacts') == []
              and finite(e.get('lowest_point_clearance_cm')) and e['lowest_point_clearance_cm'] >= 10
              and finite(e.get('tilt_degrees')) and 0 <= e['tilt_degrees'] <= 15
              and finite(e.get('speed_cm_s')) and 0 <= e['speed_cm_s'] <= 10)
        if not good: since=None
        elif since is None: since=t
        if since is not None and t-since >= 5: return True
    return False


def pair_future_actions(raw_samples):
    """obs(t) -> real acknowledged target at next sample; drop unpaired tail."""
    import copy
    result=[]
    for before, after in zip(raw_samples,raw_samples[1:]):
        ack=after.get('latest_action_ack')
        if not ack or ack.get('action_applied') is not True:
            raise ValueError('Missing acknowledged future action')
        t=ack['action_unix']
        if not finite(t) or not before['measured_unix'] < t <= after['measured_unix']:
            raise ValueError('Stale, future, or same-observation action cannot teach next action')
        s=copy.deepcopy(before)
        s.update(action_unix=t,action_applied=True,
                 state=encode_state(s['measured_joints']),
                 action=encode_state(ack['applied_target_joints']))
        result.append(s)
    return result


def export(raw, destination, *, allow_unverified=False):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from PIL import Image
    raw=Path(raw); out=Path(destination)
    manifest=json.loads((raw/'episode.json').read_text())
    if manifest.get('error') or manifest.get('incomplete_sample') or manifest.get('capture_complete') is False:
        raise ValueError('Recording did not complete cleanly')
    samples=[json.loads(p.read_text()) for p in sorted((raw/'samples').glob('*/sample.json'))]
    samples=pair_future_actions(samples)
    validate_samples(samples,raw)
    positive=not manifest.get('synthetic_test_only',False) and positive_eligibility(samples,manifest.get('review',{}))
    if not positive and not allow_unverified: raise ValueError('Not eligible as positive demonstration')
    if out.exists(): raise ValueError('Destination must not exist')
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:
        import imageio_ffmpeg
        ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
    n=len(samples); names=[key+f'_{i}' for key,(a,b) in SPANS.items() for i in range(b-a)]
    features={key:{'dtype':'float32','shape':[32],'names':names} for key in ('action','observation.state')}
    for role in ROLES:
        key=EMBODIMENT['video'][role]; first=raw/samples[0]['images'][role]
        with Image.open(first) as im: width,height=im.size
        for s in samples:
            with Image.open(raw/s['images'][role]) as im:
                if im.size != (width,height): raise ValueError('Changed camera resolution')
        features[key]={'dtype':'video','shape':[height,width,3],'names':['height','width','channel'],
                       'info':{'video.fps':FPS,'video.codec':'h264','video.pix_fmt':'yuv420p','video.is_depth_map':False,'has_audio':False}}
        dest=out/'videos/chunk-000'/key/'episode_000000.mp4';dest.parent.mkdir(parents=True,exist_ok=True)
        # Feed precisely one image per measured sample: no fps filter/interpolation.
        proc=subprocess.Popen([ffmpeg,'-v','error','-f','image2pipe','-framerate',str(FPS),'-vcodec','mjpeg','-i','pipe:0','-an','-c:v','libx264','-pix_fmt','yuv420p','-fps_mode','passthrough',str(dest)],stdin=subprocess.PIPE)
        try:
            for s in samples: proc.stdin.write((raw/s['images'][role]).read_bytes())
            proc.stdin.close()
            if proc.wait()!=0: raise RuntimeError('Video encode failed')
        finally:
            if proc.poll() is None: proc.kill();proc.wait()
    meta=out/'meta';meta.mkdir(parents=True)
    info={'codebase_version':'v2.1','robot_type':'maker_arm_simulation','fps':FPS,'chunks_size':1000,'total_episodes':1,'total_frames':n,'total_tasks':1,'total_videos':3,'total_chunks':1,'splits':{'train':'0:1'},'data_path':'data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet','video_path':'videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4','features':features}
    (meta/'info.json').write_text(json.dumps(info))
    (meta/'episodes.jsonl').write_text(json.dumps({'episode_index':0,'tasks':[manifest['task']],'length':n})+'\n')
    (meta/'tasks.jsonl').write_text(json.dumps({'task_index':0,'task':manifest['task']})+'\n')
    start=samples[0]['measured_unix']
    rows={'action':[s['action'] for s in samples],'observation.state':[s['state'] for s in samples], 'timestamp':[s['measured_unix']-start for s in samples], 'frame_index':list(range(n)),'episode_index':[0]*n,'task_index':[0]*n,'index':list(range(n))}
    data=out/'data/chunk-000/episode_000000.parquet';data.parent.mkdir(parents=True)
    pq.write_table(pa.table(rows),data)
    # Provenance stays outside the dataset: SDK rejects extra arbitrary files.
    provenance={'positive_eligible':positive,'synthetic_test_only':manifest.get('synthetic_test_only',False),'source':str(raw.resolve()),'action_alignment':'obs_t to latest actual target acknowledged after t and before obs_t+1; final unpaired observation omitted','recorded_fps':FPS,'service_resamples_to_fps':30,'timestamp_policy':'Measured timestamps preserved; video uses one original frame per fixed-rate sample','review':manifest.get('review',{}),'embodiment':EMBODIMENT}
    out.with_suffix('.provenance.json').write_text(json.dumps(provenance,indent=2))
    from dream.datasets import validate_dataset
    report=validate_dataset(out,EMBODIMENT)
    if not report.ok: raise ValueError('Official local validator: '+str(report.error))
    return provenance


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('raw');p.add_argument('destination');p.add_argument('--allow-unverified',action='store_true',help='Local format test only; never labels data positive')
    a=p.parse_args();print(json.dumps(export(a.raw,a.destination,allow_unverified=a.allow_unverified),indent=2))
