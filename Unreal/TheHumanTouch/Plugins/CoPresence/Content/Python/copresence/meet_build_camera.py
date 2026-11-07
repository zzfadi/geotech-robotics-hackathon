"""Frame the agent's latest build edits without changing other users' viewports."""
import math
import time
import unreal
from copresence import tracking, animation, toolset
handle = None
original = None
pending = []
last_result = None
due = 0.0
status = {'state':'idle'}
BUILD_MAPS = {'/Game/Maps/Tomorrowland', '/Game/Maps/MultiuserAI_UE'}

def supports_world(path):
    return path.split('.')[0] in BUILD_MAPS

def plan(boxes, camera):
    low=[min(c[i]-e[i] for c,e in boxes) for i in range(3)]
    high=[max(c[i]+e[i] for c,e in boxes) for i in range(3)]
    center=[(a+b)/2 for a,b in zip(low,high)]
    radius=max(math.dist(low,high)/2,120)
    # 90-degree horizontal FOV, 16:9 stream, with room around the work.
    distance=radius/math.sin(math.atan(1/(16/9)))*1.2
    yaw=math.degrees(math.atan2(center[1]-camera[1],center[0]-camera[0]))
    elevation=min(45,max(25,-camera[3]))
    horizontal=distance*math.cos(math.radians(elevation))
    pose=[center[0]-horizontal*math.cos(math.radians(yaw)),center[1]-horizontal*math.sin(math.radians(yaw)),center[2]+distance*math.sin(math.radians(elevation)),-elevation,yaw]
    return center,pose,distance

def observe(outcome):
    global pending,due
    if outcome.get('error') or outcome.get('rejected'):return
    changes=outcome.get('changes') or {}
    paths=[]
    for entry in changes.get('created',[])+changes.get('modified',[]):
        state=entry.get('after') or {}
        before=entry.get('before')
        if before and all(key in before and key in state for key in ('location','rotation','scale')):
            # Exclude autonomous idle/bobbing motion captured during another edit.
            travel=math.dist(before['location'],state['location'])
            turn=max(abs((a-b+180)%360-180) for a,b in zip(before['rotation'],state['rotation']))
            scale=max(abs(a-b) for a,b in zip(before['scale'],state['scale']))
            if travel<60 and turn<10 and scale<.05:continue
        if state.get('label','').startswith(('GeoTechEyes','Build_Companion','CoPresence')):continue
        if state.get('class','').endswith(('Light','Volume')):continue
        paths.append(entry['path'])
    if paths:
        pending=list(dict.fromkeys(paths))[-20:]
        due=time.monotonic()+.6

def install():
    global handle,original
    if handle is not None:return dict(status)
    original=tracking.run_tracked
    def tracked(*args,**kwargs):
        result=original(*args,**kwargs)
        observe(result)
        return result
    tracking.run_tracked=tracked
    handle=unreal.register_slate_post_tick_callback(tick)
    status.update(state='watching')
    return dict(status)

def tick(dt):
    global pending,last_result
    try:
        result=animation.last_result()
        if result is not None and result is not last_result:
            last_result=result;observe(result)
        world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
        if world is None or not supports_world(world.get_path_name()) or not tracking.current_task.get('id'):
            pending=[];return
        if not pending or time.monotonic()<due or animation.is_running() or toolset._flight['callback'] is not None:return
        paths=pending;pending=[]
        actors=[];boxes=[]
        for path in paths:
            try:
                actor=tracking.find_actor(path)
                c,e=actor.get_actor_bounds(False)
                if max(e.x,e.y,e.z)>5000 or max(e.x,e.y,e.z)<1:continue
                actors.append(actor);boxes.append(([c.x,c.y,c.z],[e.x,e.y,e.z]))
            except Exception:continue
        if not boxes:return
        location,rotation=tracking.camera_info()
        camera=[location.x,location.y,location.z,rotation.pitch,rotation.yaw]
        center,pose,distance=plan(boxes,camera)
        status.update(actors=len(actors), focus=center)
        # Following a selected agent belongs to the user. Keep its work marker
        # current without fighting that viewport with a second camera controller.
        from copresence import agents_hud
        if agents_hud._state.get('following'):
            status.update(state='following_agent');return
        # Keep a comfortable existing shot; don't chase tiny adjustments.
        if math.dist(camera[:3],pose[:3])<100 and abs((pose[4]-camera[4]+180)%360-180)<8:return
        start=unreal.Vector(*center);end=unreal.Vector(*pose[:3])
        hit=unreal.SystemLibrary.line_trace_single(world,start,end,unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,False,actors,unreal.DrawDebugTrace.NONE,True)
        if hit is not None and hit.to_tuple()[0]:
            status.update(state='occluded',actors=len(actors));return
        seconds=min(3.0,max(1.4,math.dist(camera[:3],pose[:3])/700))
        toolset.CoPresenceTools.fly_to(*pose,seconds)
        status.update(state='framed',actors=len(actors),focus=center,seconds=seconds)
    except Exception as error:
        status.update(state='error',error=str(error))

def shutdown():
    global handle,original,pending
    if handle is not None:unreal.unregister_slate_post_tick_callback(handle)
    if original is not None:tracking.run_tracked=original
    handle=None;original=None;pending=[]
