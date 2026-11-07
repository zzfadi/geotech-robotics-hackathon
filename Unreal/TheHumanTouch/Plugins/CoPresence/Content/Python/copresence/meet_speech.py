"""Reusable numeric-viseme facial animation; call only through scoped CoPresence."""
import math, time, numbers, unreal
from copresence import meet_motion
E = unreal.LevelSequenceEditorBlueprintLibrary
FPS = 60
PATH = '/Game/Agent/LS_GeoVerseSpeechRuntime'
FACE_RIG = '/Game/MetaHumans/Common/Face/Face_ControlBoard_CtrlRig'
POSES = {0:(0,0,0,0),1:(.48,0,0,.05),2:(.52,.1,0,0),4:(.34,0,0,.12),6:(.22,0,0,.24),7:(.25,.65,0,0),12:(.30,0,0,0),13:(.25,.30,0,0),14:(.20,0,0,.10),15:(.16,0,.10,.08),17:(.10,0,.32,0),19:(.16,0,0,.06),20:(.24,0,0,.06),21:(.02,0,.85,0)}
# Additional SAPI categories use approximate shapes; tested categories above are unchanged.
POSES.update({3:(.52,.35,0,0),5:(.30,.25,0,0),8:(.30,.55,0,0),9:(.50,.25,0,0),10:(.35,.35,0,0),11:(.45,0,0,.12),16:(.15,.45,0,0),18:(.15,0,0,.05)})
NAMES = ['CTRL_C_jaw.Y'] + [f'CTRL_{s}_mouth_{c}' for s in ['L','R'] for c in ['funnelU','funnelD','lipsTogetherU','lipsTogetherD','cornerPull']]
start_at = None
handle = None
original = []
prepared = False
seq = None
duration = None
end_frame = None
began_mono = None
audio_only = False
status = {'state':'idle','start_at':None,'actual_start':None,'error':None}
def numeric(value, label):
    if isinstance(value, bool) or not isinstance(value, numbers.Real) or not math.isfinite(float(value)):
        raise ValueError(label + ' must be a finite number, not bool')
    return float(value)
def validate(cues, wav_duration):
    d = numeric(wav_duration, 'duration')
    if d <= 0 or d*FPS >= 2147483646:
        raise ValueError('duration must be positive and fit Sequencer frame range')
    if not isinstance(cues, (list, tuple)):
        raise ValueError('cues must be a list of [seconds,SAPI_id,duration]')
    clean = []
    previous = -1.0
    for i, cue in enumerate(cues):
        if not isinstance(cue, (list, tuple)) or len(cue) != 3:
            raise ValueError('cue '+str(i)+' must contain exactly three numbers')
        t, v, length = [numeric(x, 'cue '+str(i)) for x in cue]
        if v != int(v) or int(v) not in POSES:
            raise ValueError('SAPI id must be integer 0 through 21')
        if t < 0 or length < 0 or t+length > d+1e-6:
            raise ValueError('cue range must lie within WAV duration')
        if t < previous:
            raise ValueError('cues must be time ordered; equal times are merged')
        previous = t
        clean.append((t,int(v),length))
    return clean,d
def idle_guard():
    if prepared or start_at is not None:
        raise RuntimeError('Speech is armed/playing; cancel first')
    if E.is_playing() or E.get_current_level_sequence() is not None:
        raise RuntimeError('Refusing another open/playing Sequencer')
def live_avatar():
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    actors = [a for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors() if a.get_actor_label() == 'MH_GeoVerseAgent_Meet']
    if len(actors) != 1:
        raise RuntimeError('Expected one live MH_GeoVerseAgent_Meet')
    actor = actors[0]
    faces = [c for c in actor.get_components_by_class(unreal.SkeletalMeshComponent) if c.get_name() == 'Face']
    if len(faces) != 1:
        raise RuntimeError('Expected one live Face component')
    return world, actor, faces[0]
def prepare(cues, wav_duration):
    global seq,duration,end_frame,audio_only
    clean,d = validate(cues,wav_duration)
    idle_guard()
    world_path = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world().get_path_name()
    audio_only = world_path.split('.')[0] in {'/Game/Maps/'+name for name in ('Tomorrowland', 'Rubes_Kitchen', 'Kitchen_Env', 'AssetZoo', 'L1_Incline_Plane', 'L1_IP_Challange', 'L2_Lever', 'L3_Wedge', 'L4_Wheel_Axle', 'L5_Pulley', 'L5_Pulley_Challenge', 'L6_Screw')}
    if audio_only:
        duration,end_frame=d,int(math.ceil(d*FPS))
        status.clear();status.update(state='ready',mode='eyes',duration=d,error=None)
        return dict(status)
    world,actor,face = live_avatar()
    rig_bp = unreal.load_asset(FACE_RIG)
    if rig_bp is None:
        raise RuntimeError('Face_ControlBoard_CtrlRig unavailable')
    target = unreal.load_asset(PATH) if unreal.EditorAssetLibrary.does_asset_exist(PATH) else unreal.AssetToolsHelpers.get_asset_tools().create_asset('LS_GeoVerseSpeechRuntime','/Game/Agent',unreal.LevelSequence,unreal.LevelSequenceFactoryNew())
    if target is None or not isinstance(target,unreal.LevelSequence):
        raise RuntimeError('Runtime sequence creation/type failed')
    target.modify()
    for binding in list(target.get_bindings())[::-1]:
        binding.remove()
    # ControlRig track creation requires this sequence to be open in UE 5.8.
    if not E.open_level_sequence(target):
        raise RuntimeError('Could not open generated animation sequence')
    try:
        final = int(math.ceil(d*FPS))
        target.set_display_rate(unreal.FrameRate(FPS,1))
        target.set_playback_start(0)
        target.set_playback_end(final+1)
        actor_binding = target.add_possessable(actor)
        body = next(c for c in actor.get_components_by_class(unreal.SkeletalMeshComponent) if c.get_name() == 'Body')
        body_binding = target.add_possessable(body)
        body_binding.set_parent(actor_binding)
        body_rig = unreal.load_asset('/Game/MetaHumans/Common/Common/MetaHuman_ControlRig')
        body_track = unreal.ControlRigSequencerLibrary.find_or_create_control_rig_track(world,target,body_rig.get_control_rig_class(),body_binding)
        body_section = body_track.get_sections()[0]
        body_section.set_range(0,final+1)
        meet_motion.add_head_keys({str(c.channel_name):c for c in body_section.get_all_channels()},final,FPS,True)
        face_binding = target.add_possessable(face)
        face_binding.set_parent(actor_binding)
        track = unreal.ControlRigSequencerLibrary.find_or_create_control_rig_track(world,target,rig_bp.get_control_rig_class(),face_binding)
        if track is None:
            raise RuntimeError('Face rig track creation failed')
        section = track.get_sections()[0]
        section.modify()
        section.set_range(0,final+1)
        channels = {str(c.channel_name):c for c in section.get_all_channels()}
        missing = [n for n in NAMES if n not in channels]
        if missing:
            raise RuntimeError('Missing facial channels: '+repr(missing))
        meet_motion.add_face_keys(channels,final,FPS)
        groups = {}
        for t,v,length in clean:
            item = groups.setdefault(t,{'duration':length,'pose':[0,0,0,0]})
            item['duration'] = max(item['duration'],length)
            item['pose'] = [max(x,y) for x,y in zip(item['pose'],POSES[v])]
        keys = {n:{0:0.0,final:0.0} for n in NAMES}
        for t,item in sorted(groups.items()):
            start = min(final,int(round(t*FPS)))
            end = min(final,max(start+2,int(round((t+item['duration'])*FPS))))
            jaw,funnel,closed,wide = item['pose']
            for n in NAMES:
                value = jaw if n == 'CTRL_C_jaw.Y' else funnel if 'funnel' in n else closed if 'Together' in n else wide
                keys[n][max(0,start-1)] = 0.0
                keys[n][start] = value
                if end > start:
                    keys[n][end-1] = value
                keys[n][end] = 0.0
        for n in NAMES:
            keys[n][final] = 0.0
            channel = channels[n]
            for key in channel.get_keys():
                channel.remove_key(key)
            for frame,value in sorted(keys[n].items()):
                channel.add_key(unreal.FrameNumber(frame),value,interpolation=unreal.MovieSceneKeyInterpolation.LINEAR)
        seq,duration,end_frame = target,d,final
        status.clear()
        status.update(state='ready',start_at=None,actual_start=None,error=None,duration=d,end_frame=final,cues=len(clean),keys=sum(len(v) for v in keys.values()),sequence=target.get_path_name())
        return dict(status)
    finally:
        E.close_level_sequence()
def _restore():
    global prepared,original,start_at,began_mono
    errors = []
    if prepared:
        try:
            E.pause()
            E.set_current_time(end_frame)
            E.force_update()
            E.close_level_sequence()
        except Exception as exc:
            errors.append(repr(exc))
        for component,flag in original:
            try:
                component.set_update_animation_in_editor(flag)
            except Exception as exc:
                errors.append(repr(exc))
    prepared,original,start_at,began_mono = False,[],None,None
    status['start_at'] = None
    if errors:
        raise RuntimeError('; '.join(errors))
def cancel():
    try:
        _restore()
        status.update(state='cancelled',error=None,finished_at=time.time())
    except Exception as exc:
        status.update(state='error',error=repr(exc))
def ensure_live_bindings(actor):
    """Resolve editor-world possessables only after opening our sequence."""
    if E.get_current_level_sequence() != seq:
        raise RuntimeError('Speech binding check requires its current sequence')
    sub = unreal.get_editor_subsystem(unreal.LevelSequenceEditorSubsystem)
    def bound(binding):
        bid = unreal.MovieSceneObjectBindingID()
        bid.set_editor_property('guid',binding.get_id())
        return list(E.get_bound_objects(bid))
    roots = [b for b in seq.get_bindings() if b.get_name() == actor.get_actor_label()]
    if len(roots) != 1:
        raise RuntimeError('Speech actor binding is missing or ambiguous')
    if actor not in bound(roots[0]):
        sub.replace_binding_with_actors([actor],roots[0])
    for name in ('Body','Face'):
        component = next(c for c in actor.get_components_by_class(unreal.SkeletalMeshComponent) if c.get_name() == name)
        candidates = [b for b in seq.get_bindings() if b.get_name() == name and b.get_tracks()]
        if len(candidates) != 1:
            raise RuntimeError('Speech rig binding is missing or ambiguous: '+name)
        if component not in bound(candidates[0]):
            sub.rebind_component(candidates,name)
        candidates = [b for b in seq.get_bindings() if b.get_name() == name and b.get_tracks()]
        if len(candidates) != 1 or component not in bound(candidates[0]):
            raise RuntimeError('Speech binding did not resolve: '+name)
    roots = [b for b in seq.get_bindings() if b.get_name() == actor.get_actor_label()]
    if len(roots) != 1 or actor not in bound(roots[0]):
        raise RuntimeError('Speech actor binding did not resolve')
def arm(epoch):
    global start_at,original,prepared,began_mono
    epoch = numeric(epoch,'epoch')
    if epoch <= time.time():
        raise ValueError('Epoch is late; no automatic reschedule')
    idle_guard()
    if audio_only:
        start_at,began_mono=epoch,None
        status.update(state='armed',start_at=epoch,actual_start=None,error=None)
        return
    if seq is None or duration is None:
        raise RuntimeError('Call prepare(cues,duration) first')
    world,actor,face = live_avatar()
    original = [(c,bool(c.get_editor_property('update_animation_in_editor'))) for c in actor.get_components_by_class(unreal.SkeletalMeshComponent)]
    prepared = True
    try:
        if not E.open_level_sequence(seq):
            raise RuntimeError('Sequencer open failed')
        ensure_live_bindings(actor)
        for component,flag in original:
            component.set_update_animation_in_editor(True)
        E.set_current_time(end_frame)
        E.force_update()
        if epoch <= time.time():
            raise RuntimeError('Epoch passed during preparation')
        start_at,began_mono = epoch,None
        status.update(state='armed',start_at=epoch,actual_start=None,error=None)
    except Exception:
        _restore()
        raise
def tick(dt):
    global began_mono
    try:
        if start_at is None:
            return
        if status['state'] == 'armed' and time.time() >= start_at:
            if not audio_only:
                E.set_current_time(0)
                E.play()
            if not audio_only and not E.is_playing():
                raise RuntimeError('Sequencer playback failed')
            actual = time.time()
            began_mono = time.monotonic()
            status.update(state='playing',actual_start=actual,lateness_ms=(actual-start_at)*1000)
        elif status['state'] == 'playing' and time.monotonic()-began_mono >= duration:
            _restore()
            status.update(state='done',finished_at=time.time())
    except Exception as exc:
        failure = repr(exc)
        try:
            _restore()
        except Exception as restore_error:
            failure += '; restore: '+repr(restore_error)
        status.update(state='error',error=failure)
def initialize():
    global handle
    if handle is not None:
        raise RuntimeError('Watcher already installed')
    handle = unreal.register_slate_post_tick_callback(tick)
def shutdown():
    global handle
    cancel()
    if handle is not None:
        unreal.unregister_slate_post_tick_callback(handle)
        handle = None
