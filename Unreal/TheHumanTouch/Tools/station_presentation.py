"""Local Quest lighting and station camera presentation. Run inside the Human Touch editor."""
import json
import math
import time
from pathlib import Path

import unreal

ROOT=Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir()))/'Capture/RoomLight'
ROOT.mkdir(parents=True,exist_ok=True)
MAP='/Game/Maps/TheHumanTouch'
POLICY=json.loads((Path(__file__).resolve().parents[1]/'Config/StationPresentation.json').read_text())
CAMERA_PROPS=set(POLICY['camera_props'])
PRACTICALS=set(POLICY['studio_fills'])
_cache={}
_current_light=None
_scan=None
_installer=None
_applied_worlds=[]
_last_check=0

def _world():
    return unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()

def _station(w):
    return w and w.get_path_name().split('.',1)[0].rsplit('/',1)[0]=='/Game/Maps' and w.get_name().split('_')[-1]=='TheHumanTouch'

def _actors(w):
    return unreal.GameplayStatics.get_all_actors_of_class(w,unreal.Actor)

def _vec(v):return [v.x,v.y,v.z]

def _axes(r):
    m=unreal.MathLibrary
    return [m.get_forward_vector(r),m.get_right_vector(r),m.get_up_vector(r)]

def _profile():
    p=ROOT/'active.json'
    if not p.is_file():return None
    return json.loads(p.read_text())

def _cubemap(profile):
    hdr=ROOT/Path(profile['hdr']).name
    if not hdr.is_file():raise RuntimeError('Local light probe file is missing')
    if hdr in _cache:return _cache[hdr]
    name='Room_'+str(hdr.stat().st_mtime_ns)
    existing=unreal.find_object(None,'/Temp/HumanTouchLight/'+name+'.'+name)
    if isinstance(existing,unreal.TextureCube):
        _cache[hdr]=existing
        return existing
    job=unreal.AssetImportTask()
    job.filename=str(hdr);job.destination_path='/Temp/HumanTouchLight';job.destination_name='Room_'+str(hdr.stat().st_mtime_ns)
    job.automated=True;job.save=False
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([job])
    assets=job.get_objects()
    if len(assets)!=1 or not isinstance(assets[0],unreal.TextureCube):raise RuntimeError('Could not construct the measured room cubemap')
    _cache[hdr]=assets[0]
    return assets[0]

def _exposure(volume,bias):
    settings=volume.settings
    for name,value in [('auto_exposure_bias',bias),('bloom_intensity',POLICY['bloom_intensity'])]:
        settings.set_editor_property('override_'+name,True)
        settings.set_editor_property(name,value)
    volume.set_editor_property('settings',settings)

def apply(world=None,profile=None):
    global _current_light
    world=world or _world()
    if not _station(world):return
    cube=_cubemap(profile) if profile else None
    gain=profile.get("gain",40.) if profile else POLICY["sky_intensity"]
    count=0; feeds=0
    for a in _actors(world):
        n=a.get_actor_label(); leaf=n.rsplit('__',1)[-1]
        optical=isinstance(a,unreal.CameraActor) and leaf.startswith('MakerCamera_')
        if optical or leaf in CAMERA_PROPS:
            feeds+=int(optical)
            for c in a.get_components_by_class(unreal.PrimitiveComponent):
                if leaf in CAMERA_PROPS or c.get_class().get_name() in ('CameraProxyMeshComponent','DrawFrustumComponent','BillboardComponent'):
                    c.set_visibility(False,False);c.set_hidden_in_game(True,False);count+=1
        if leaf in PRACTICALS and isinstance(a,unreal.Light):
            a.light_component.set_intensity(0.)
        if n=='MakerLighting_GlobalDaylight':
            c=a.light_component;c.set_cast_shadows(True)
            if profile:
                # Environment captures carry diffuse and reflections. Add a separate shadow key only for a measured lobe.
                if profile['qualified_key']:
                    x,y,z=profile['direction'];a.set_actor_rotation(unreal.Rotator(pitch=-math.degrees(math.asin(z)),yaw=math.degrees(math.atan2(-y,-x)),roll=0),False)
                    c.set_light_color(unreal.LinearColor(*profile['key_color'],1),False)
                    c.set_editor_property('light_source_angle',min(35.,max(2.,profile['angular_spread_deg'])))
                c.set_intensity(gain*(2*math.pi/13)*max(profile['key_to_fill']-1,0) if profile['qualified_key'] else 0.)
            else:c.set_intensity(POLICY['daylight_lux'])
        if n=='MakerLighting_Ambient':
            c=a.light_component
            c.set_editor_property('source_type',unreal.SkyLightSourceType.SLS_SPECIFIED_CUBEMAP if cube else unreal.SkyLightSourceType.SLS_CAPTURED_SCENE)
            if cube:c.set_cubemap(cube)
            c.set_intensity(gain)
            c.recapture_sky()
        if n=='MakerLighting_Exposure':_exposure(a,float(profile.get('bias',POLICY['exposure_bias'])) if profile else POLICY['exposure_bias'])
    if world==_world():
        _current_light=profile
    if profile: _balance(world,gain,profile.get("balance",[1.,1.,1.]),profile)
    return {'camera_meshes_hidden':count,'camera_feeds_preserved':feeds,'measured_probe':bool(profile)}

def activate():
    return apply(profile=_profile())

def apply_measurement():
    return apply(profile=json.loads((ROOT/'measurement.json').read_text()))

def _balance(world,gain,balance,p):
    for a in _actors(world):
        n=a.get_actor_label()
        if n=="MakerLighting_Ambient":
            maximum=max(balance)
            a.light_component.set_light_color(unreal.LinearColor(*[v/maximum for v in balance],1))
            a.light_component.set_intensity(gain*maximum)
        if n=="MakerLighting_GlobalDaylight" and p["qualified_key"]:
            rgb=[x*y for x,y in zip(p["key_color"],balance)]
            maximum=max(rgb)
            a.light_component.set_light_color(unreal.LinearColor(*[v/maximum for v in rgb],1),False)
            a.light_component.set_intensity(gain*maximum*(2*math.pi/13)*max(p["key_to_fill"]-1,0))

def set_gain(gain,balance=None):
    p=_current_light or _profile()
    balance=balance if balance is not None else p.get("balance",[1.,1.,1.]) if p else []
    if not p or not .125<=gain<=1024 or len(balance)!=3 or not all(.2<=v<=5 for v in balance):
        raise ValueError("missing room profile or invalid light balance")
    _balance(_world(),gain,balance,p)

def preview(filename):
    from copresence import ui_widgets
    import maker_arm_camera_qa
    ui_widgets.after(.1,lambda:maker_arm_camera_qa.capture(str(ROOT/filename),'MakerInteraction_T09__MakerCamera_Overview'))

def bench():
    w=_world()
    actors={a.get_actor_label():a for a in _actors(w)}
    prefix='MakerInteraction_T09__'
    table=actors[prefix+'Dreamlabs_YAM_Table'];c,e=table.get_actor_bounds(False)
    cam=actors[prefix+'MakerCamera_Overview']
    return dict(top_cm=[c.x,c.y,c.z+e.z],across=_vec(table.get_actor_forward_vector()),
        bases_cm=[_vec(actors[prefix+'MakerArm_'+s+'_base_link'].get_actor_location()) for s in ('left','right')],
        overview=dict(pos=_vec(cam.get_actor_location()),axes=[_vec(v) for v in _axes(cam.get_actor_rotation())],
        fov=cam.get_component_by_class(unreal.CameraComponent).field_of_view))

def begin_scan(seconds=60):
    global _scan
    if _scan:unreal.unregister_slate_post_tick_callback(_scan);_scan=None
    w=_world(); history=[];last=0.;deadline=time.monotonic()+seconds
    (ROOT/'bench.json').write_text(json.dumps(bench()))
    def tick(_dt):
        nonlocal last
        global _scan
        if time.monotonic()>deadline or _world()!=w:
            unreal.unregister_slate_post_tick_callback(_scan);_scan=None;return
        now=time.time()
        if now-last<.04:return
        last=now
        avatars=unreal.GameplayStatics.get_all_actors_of_class(w,unreal.XRCreativeAvatar)
        h=unreal.HeadMountedDisplayFunctionLibrary
        if len(avatars)!=1 or not h.has_valid_tracking_position():return
        r,v=h.get_orientation_and_position();t=avatars[0].get_actor_transform()
        history.append([now,_vec(t.transform_location(v)),[_vec(t.transform_direction(x)) for x in _axes(r)],_vec(avatars[0].get_actor_location()),avatars[0].get_actor_rotation().yaw]);del history[:-90]
        p=ROOT/'pose.tmp';p.write_text(json.dumps(history));p.replace(ROOT/'pose.json')
    _scan=unreal.register_slate_post_tick_callback(tick)

def install():
    global _installer
    if _installer:return
    def on_tick(_dt):
        global _last_check
        if time.monotonic()-_last_check<1.:return
        _last_check=time.monotonic()
        subsystem=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        worlds=[w for w in (subsystem.get_editor_world(),subsystem.get_game_world()) if _station(w)]
        live_keys=[(w.get_path_name(),hash(w)) for w in worlds]
        _applied_worlds[:]=[key for key in _applied_worlds if key in live_keys]
        for w,key in zip(worlds,live_keys):
            if key not in _applied_worlds:
                _applied_worlds.append(key)
                try:apply(w,_profile())
                except Exception as exc:unreal.log_warning('Station lighting: '+str(exc));apply(w)
    _installer=unreal.register_slate_post_tick_callback(on_tick)
