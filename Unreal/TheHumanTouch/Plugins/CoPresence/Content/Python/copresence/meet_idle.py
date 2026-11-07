"""Scoped-installed MetaHuman idle; initializes only on explicit lifecycle call."""
import math, random, time, unreal
from copresence import meet_speech as speech
from copresence import meet_motion
E = unreal.LevelSequenceEditorBlueprintLibrary
PATH = '/Game/Agent/LS_GeoVerseIdle'
handle = None
owns = False
waiting = False
stopping = False
preview_frame = None
hold_until = 0.0
last_eval = 0.0
status = {'state':'uninitialized','error':None}
def build():
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    seq = unreal.load_asset(PATH) if unreal.EditorAssetLibrary.does_asset_exist(PATH) else unreal.AssetToolsHelpers.get_asset_tools().create_asset('LS_GeoVerseIdle','/Game/Agent',unreal.LevelSequence,unreal.LevelSequenceFactoryNew())
    seq.modify()
    for b in list(seq.get_bindings())[::-1]:
        b.remove()
    # ControlRig track creation requires this sequence to be open in UE 5.8.
    if not E.open_level_sequence(seq):
        raise RuntimeError('Could not open generated animation sequence')
    try:
        seq.set_display_rate(unreal.FrameRate(60,1))
        seq.set_playback_start(0)
        seq.set_playback_end(3601)
        ab = seq.add_possessable(actor)
        channels = {}
        for name,path in [('Body','/Game/MetaHumans/Common/Common/MetaHuman_ControlRig'),('Face','/Game/MetaHumans/Common/Face/Face_ControlBoard_CtrlRig')]:
            component = next(c for c in components if c.get_name() == name)
            b = seq.add_possessable(component)
            b.set_parent(ab)
            bp = unreal.load_asset(path)
            track = unreal.ControlRigSequencerLibrary.find_or_create_control_rig_track(world,seq,bp.get_control_rig_class(),b)
            section = track.get_sections()[0]
            section.set_range(0,3601)
            channels[name] = {str(c.channel_name):c for c in section.get_all_channels()}
        meet_motion.add_head_keys(channels['Body'],3600)
        times = meet_motion.add_face_keys(channels['Face'],3600)
        return seq,times
    finally:
        E.close_level_sequence()
def restore_flags():
    for c,flag in original:
        try:
            c.set_update_animation_in_editor(flag)
        except Exception as error:
            # Map reload destroys old components; there is nothing left to restore.
            if 'ObjectInstance is null' not in str(error):
                raise
def release():
    global owns
    current = E.get_current_level_sequence()
    if owns and current == sequence:
        E.pause()
        E.set_current_time(0)
        E.force_update()
        E.close_level_sequence()
        restore_flags()
    elif current is None:
        restore_flags()
    owns = False
def suspend(seconds=1.0):
    global hold_until,waiting
    release()
    waiting = True
    hold_until = time.monotonic()+max(0.0,float(seconds))
    status['state'] = 'suspended'
def speech_guard():
    suspend(1.0)
    return original_guard()
def ensure_live_bindings():
    """Repair only unresolved possessables, after opening this sequence."""
    if E.get_current_level_sequence() != sequence:
        raise RuntimeError('Idle binding check requires its current sequence')
    def bound(binding):
        bid = unreal.MovieSceneObjectBindingID()
        bid.set_editor_property('guid', binding.get_id())
        return list(E.get_bound_objects(bid))
    sub = unreal.get_editor_subsystem(unreal.LevelSequenceEditorSubsystem)
    roots = [b for b in sequence.get_bindings() if b.get_name() == actor.get_actor_label()]
    if len(roots) != 1:
        raise RuntimeError('Idle actor binding is missing or ambiguous')
    if actor not in bound(roots[0]):
        sub.replace_binding_with_actors([actor], roots[0])
    for name in ('Body', 'Face'):
        component = next(c for c in components if c.get_name() == name)
        # Actor replacement can leave empty proxy bindings; only rig-bearing
        # bindings drive animation. Reacquire proxies after each repair.
        candidates = [b for b in sequence.get_bindings()
                      if b.get_name() == name and b.get_tracks()]
        if len(candidates) != 1:
            raise RuntimeError('Idle rig binding is missing or ambiguous: ' + name)
        if component not in bound(candidates[0]):
            sub.rebind_component(candidates, name)
        candidates = [b for b in sequence.get_bindings()
                      if b.get_name() == name and b.get_tracks()]
        if len(candidates) != 1 or component not in bound(candidates[0]):
            raise RuntimeError('Idle binding did not resolve: ' + name)
def take():
    global owns,started,last_eval,actor,components,original
    if E.get_current_level_sequence() is not None or E.is_playing():
        return False
    _, live_actor, _ = speech.live_avatar()
    live_components = live_actor.get_components_by_class(unreal.SkeletalMeshComponent)
    if live_actor != actor or list(live_components) != list(components):
        actor, components = live_actor, live_components
        original = [(c, bool(c.get_editor_property('update_animation_in_editor'))) for c in components]
    if not E.open_level_sequence(sequence):
        raise RuntimeError('Idle Sequencer open failed')
    owns = True
    ensure_live_bindings()
    for c in components:
        c.set_update_animation_in_editor(True)
    started = time.monotonic()
    last_eval = 0.0
    return True
def preview(frame):
    global preview_frame
    if speech.prepared or speech.start_at is not None:
        raise RuntimeError('Speech is busy')
    current = E.get_current_level_sequence()
    if current is not None and current != sequence:
        raise RuntimeError('Another Sequencer is open')
    if not owns and not take():
        raise RuntimeError('Could not acquire idle evaluation')
    preview_frame = max(0,min(3600,int(frame)))
    E.set_current_time(preview_frame)
    E.force_update()
def resume():
    global preview_frame,started,waiting,hold_until
    preview_frame = None
    started = time.monotonic()
    waiting = False
    hold_until = 0.0
def tick(dt):
    global owns,waiting,last_eval,handle
    try:
        current = E.get_current_level_sequence()
        other = current is not None and current != sequence
        busy = speech.prepared or speech.start_at is not None or speech.status.get('state') in ('armed','playing')
        if other:
            # Do not pause, close, seek, or change flags on somebody else's sequence.
            owns = False
            status['state'] = 'yielding_external'
            return
        if stopping:
            release()
            if speech.idle_guard is speech_guard:
                speech.idle_guard = original_guard
            if handle is not None:
                unreal.unregister_slate_post_tick_callback(handle)
                handle = None
            status['state'] = 'stopped'
            return
        if busy:
            if owns:
                release()
            status['state'] = 'yielding_speech'
            return
        now = time.monotonic()
        if waiting:
            state = speech.status.get('state')
            if now < hold_until or (state == 'ready' and now < hold_until+9):
                return
            waiting = False
        if now < hold_until:
            return
        if not owns and not take():
            return
        if now-last_eval < 1/30:
            return
        last_eval = now
        frame = preview_frame if preview_frame is not None else int(((now-started)%60)*60)
        E.set_current_time(frame)
        E.force_update()
        status.update(state='preview' if preview_frame is not None else 'idling',frame=frame,error=None)
    except Exception as exc:
        status.update(state='error',error=repr(exc))
        try:
            cleanup()
        except Exception as cleanup_error:
            status['cleanup_error'] = repr(cleanup_error)
def initialize():
    global actor,components,original,sequence,blink_times,original_guard,handle,started,stopping
    if handle is not None:
        raise RuntimeError('Idle watcher already installed')
    current = E.get_current_level_sequence()
    # Editor restoration can reopen our generated idle asset before its watcher.
    # Reclaim only this exact idle asset, never a user's unrelated sequence.
    if current is not None and current.get_path_name() == PATH+'.LS_GeoVerseIdle' and not E.is_playing() and not speech.prepared and speech.start_at is None:
        E.close_level_sequence()
    if E.get_current_level_sequence() is not None or E.is_playing() or speech.prepared or speech.start_at is not None:
        raise RuntimeError('Existing speech/Sequencer busy; idle not installed')
    actor = next(a for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors() if a.get_actor_label() == 'MH_GeoVerseAgent_Meet')
    components = actor.get_components_by_class(unreal.SkeletalMeshComponent)
    original = [(c,bool(c.get_editor_property('update_animation_in_editor'))) for c in components]
    sequence,blink_times = build()
    original_guard = speech.idle_guard
    speech.idle_guard = speech_guard
    stopping = False
    started = time.monotonic()
    handle = unreal.register_slate_post_tick_callback(tick)
    status.update(state='ready',blink_times=blink_times)
def cleanup():
    global stopping
    stopping = True
    release()
    if speech.idle_guard is speech_guard:
        speech.idle_guard = original_guard
    # If another sequence is open, defer flag restoration and unregister until it closes.
    tick(0.0)
