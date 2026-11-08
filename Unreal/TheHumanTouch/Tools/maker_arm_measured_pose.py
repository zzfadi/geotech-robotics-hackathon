"""Fresh calibrated feedback -> Table10 SIE visuals only; no hardware output.

Call from the Unreal game thread. No polling, callbacks, editor transactions,
network calls or editor-world fallback. Transport/calibration registry is owned
by Interaction. This does not replicate SIE poses to other editors implicitly.
"""
import math
import time
from maker_arm_model import model, mounted_frames, ue_pose

PREFIX = 'MakerInteraction_T09__'
ALIAS_PREFIX = 'MakerFleet_T09__'
LINKS = tuple(l.get('name') for l in model().findall('link') if l.findall('visual'))
JOINTS = tuple(j.get('name') for j in model().findall('joint') if j.get('type') == 'revolute')
MAX_GAP = .104825


def _finite_vector(value, length):
    if not isinstance(value, (tuple, list)) or len(value) != length or any(type(x) not in (int, float) or not math.isfinite(x) for x in value):
        raise ValueError('Expected a finite numeric vector of length%d' % length)
    return tuple(float(x) for x in value)


def _rotate(q, v):
    x,y,z,w = _finite_vector(q,4)
    if abs(x*x+y*y+z*z+w*w-1)>1e-5:raise ValueError('Non-unit pose quaternion')
    u=(x,y,z);dot=sum(a*b for a,b in zip(u,v));cross=(y*v[2]-z*v[1],z*v[0]-x*v[2],x*v[1]-y*v[0])
    return tuple(2*dot*u[i]+(w*w-x*x-y*y-z*z)*v[i]+2*w*cross[i] for i in range(3))


def infer_virtual_gap(wrist, left, right):
    """Preserve a verified *virtual* jaw gap, never infer hardware gripper state.

    Poses contain p(cm), q(xyzw), scale. Verify official symmetric prismatic
    geometry relative to the current wrist before any receiver setter runs.
    """
    poses=(wrist,left,right)
    for p in poses:
        _finite_vector(p['p'],3);_rotate(p['q'],(0,0,1))
        if max(abs(x-1) for x in _finite_vector(p['scale'],3))>1e-6:raise ValueError('Non-unit arm scale')
    joint=next(j for j in model().findall('joint') if j.get('name')=='gripper_left_joint')
    axis=[float(x) for x in joint.find('axis').get('xyz').split()]
    length=math.sqrt(sum(x*x for x in axis));axis=[axis[0]/length,-axis[1]/length,axis[2]/length]
    world_axis=_rotate(wrist['q'],axis)
    dl=[left['p'][i]-wrist['p'][i] for i in range(3)]
    dr=[right['p'][i]-wrist['p'][i] for i in range(3)]
    half=sum(a*b for a,b in zip(dl,world_axis))
    gap=half*.02  # cm half-width -> metres total gap
    if not -1e-8 <= gap <= MAX_GAP+1e-8:raise ValueError('Existing virtual gap outside model limits')
    if max(abs(dl[i]-half*world_axis[i]) for i in range(3))>1e-3 or max(abs(dr[i]+half*world_axis[i]) for i in range(3))>1e-3:
        raise ValueError('Existing jaws are not symmetric on the wrist axis')
    for jaw in (left,right):
        if abs(sum(a*b for a,b in zip(wrist['q'],jaw['q'])))<1-1e-8:raise ValueError('Jaw rotation differs from wrist')
    return min(MAX_GAP,max(0.,gap))


def resolve_aliases(label_actor_pairs, side):
    if side not in ('left','right'):raise ValueError('Unknown arm side')
    expected={PREFIX+'MakerArm_'+side+'_'+n:n for n in LINKS};found={}
    for label,actor in label_actor_pairs:
        if label not in expected:continue
        if label in found:raise ValueError('Duplicate Table10 arm label: '+label)
        found[label]=actor
    if set(found)!=set(expected):raise ValueError('Missing exact Table10 visual arm links')
    return {ALIAS_PREFIX+name[len(PREFIX):]:actor for name,actor in found.items()}


class PoseGate:
    def __init__(self, registrations, hz=10):
        if type(hz) not in (int,float) or not 0<hz<=10:raise ValueError('Receiver rate must be0..10Hz')
        self.registrations={}
        for side,r in registrations.items():
            if side not in ('left','right') or not all(isinstance(r.get(k),str) and r[k].strip() for k in ('serial','calibration_id')):
                raise ValueError('Explicit serial/calibration registration required')
            self.registrations[side]={k:r[k] for k in ('serial','calibration_id')}
        if not self.registrations:raise ValueError('No registered arm')
        self.interval=1/hz;self.last={}

    def validate(self, packet, now, monotonic):
        if type(now) not in (int,float) or type(monotonic) not in (int,float) or not all(math.isfinite(v) for v in (now,monotonic)):
            raise ValueError('Finite clocks required')
        side=packet.get('side');r=self.registrations.get(side)
        if not r or any(packet.get(k)!=v for k,v in r.items()):raise ValueError('Unregistered feedback identity')
        if type(packet.get('table')) is not int or packet['table']!=10 or packet.get('pose_source')!='measured_sdk_feedback' or packet.get('physical_output_enabled') is not False:
            raise ValueError('Only read-only measured Table10 feedback accepted')
        stamp=packet.get('feedback_unix')
        if type(stamp) not in (int,float) or not math.isfinite(stamp) or not 0<=now-stamp<=.25:raise ValueError('Stale or future feedback')
        if 'joint_names' in packet and tuple(packet['joint_names'])!=JOINTS:raise ValueError('Joint order mismatch')
        q=_finite_vector(packet.get('q_urdf_rad'),6);gap=packet.get('jaw_gap_m')
        if gap is not None and (type(gap) not in (int,float) or not math.isfinite(gap) or not 0<=gap<=MAX_GAP):raise ValueError('Invalid measured jaw gap')
        mounted_frames(q, gap if gap is not None else 0., yaw=math.pi)  # validate limits; zero here is not applied
        old=self.last.get(side)
        if old and stamp<=old[0]:raise ValueError('Duplicate or out-of-order measured feedback')
        if old and monotonic-old[1]+1e-9<self.interval:return None
        return {'side':side,'q':q,'gap':gap,'stamp':stamp,'monotonic':monotonic}

    def commit(self, validated):
        self.last[validated['side']]=(validated['stamp'],validated['monotonic'])


class RuntimeReceiver:
    """No editor-world writes. Owner must run SIE and provide registered feedback.

    Calibration values are not trusted from packets: caller pins expected serial
    and calibration IDs from its validated registry. Unknown jaw geometry is
    frozen relative to the moving wrist and explicitly reported as unmeasured.
    """
    def __init__(self, registrations, world, hz=10):
        import unreal
        self.unreal=unreal;self.world=world;self.closed=False;self.gate=PoseGate(registrations,hz)
        self._check_world()
        pairs=[(a.get_actor_label(),a) for a in unreal.GameplayStatics.get_all_actors_of_class(world,unreal.Actor)]
        self.aliases={side:resolve_aliases(pairs,side) for side in self.gate.registrations}

    def _check_world(self):
        u=self.unreal
        if self.closed or self.world is None or not u.SystemLibrary.is_valid(self.world) or self.world!=u.get_editor_subsystem(u.UnrealEditorSubsystem).get_game_world():
            raise RuntimeError('Measured receiver requires its current SIE/game world; no editor fallback')

    @staticmethod
    def _pose(actor):
        t=actor.get_actor_transform();p=t.translation;q=t.rotation;s=t.scale3d
        return {'p':(p.x,p.y,p.z),'q':(q.x,q.y,q.z,q.w),'scale':(s.x,s.y,s.z)}

    def apply(self, packet, now=None, monotonic=None):
        self._check_world()
        v=self.gate.validate(packet,time.time() if now is None else now,time.monotonic() if monotonic is None else monotonic)
        if v is None:return {'applied':False,'reason':'rate_limited','physical_output_enabled':False}
        side=v['side'];aliases=self.aliases[side];prefix=ALIAS_PREFIX+'MakerArm_'+side+'_'
        for link in LINKS:
            a=aliases[prefix+link]
            if not self.unreal.SystemLibrary.is_valid(a) or a.get_actor_label()!=PREFIX+'MakerArm_'+side+'_'+link:raise RuntimeError('Table10 actor binding changed')
            if max(abs(x-1) for x in self._pose(a)['scale'])>1e-6:raise ValueError('Non-unit arm scale')
        base=self._pose(aliases[prefix+'base_link'])['p'];expected=(2480,-40 if side=='left' else 40,76)
        expected_rotation=ue_pose(mounted_frames(v['q'],0.,yaw=math.pi)['base_link'])[1]
        base_rotation=self._pose(aliases[prefix+'base_link'])['q']
        if max(abs(a-b) for a,b in zip(base,expected))>.001 or abs(sum(a*b for a,b in zip(base_rotation,expected_rotation)))<1-1e-8:raise ValueError('Table10 base registration moved')
        gap=v['gap']
        if gap is None:gap=infer_virtual_gap(*(self._pose(aliases[prefix+n]) for n in ('link_007','gripper_left','gripper_right')))
        from maker_arm_fleet import set_pose
        moved=set_pose(9,side,v['q'],gap,existing=aliases)
        self.gate.commit(v)
        return {'applied':True,'moved_links':moved,'side':side,'feedback_unix':v['stamp'],
                'virtual_jaw_gap_m':gap,'jaw_source':'preserved_virtual_geometry' if v['gap'] is None else 'measured_feedback',
                'jaw_measured':v['gap'] is not None,'physical_output_enabled':False,'scope':'local SIE visuals only'}

    def close(self):
        self.closed=True
