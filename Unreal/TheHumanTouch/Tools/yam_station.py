"""Editor-side YAM station. Invoke through scoped CoPresence run_python transactions.
No hardware/API calls. Mesh assets must be imported before build().
"""
import unreal
from yam_kinematics import frames, ue_pose

ASSETS='/Game/_Agents/Denys_Mac_Codex/YAM'
PREFIX='Dreamlabs_YAM_'
# Layout estimates from user photos, centimetres. Two identical six-axis arms.
BASES={'left':(20,-40,76),'right':(20,40,76)}
REST=[0,2.0,1.4,-0.8,0,0]

def actors():
    return {a.get_actor_label():a for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()}

def material(name,color):
    path=ASSETS+'/M_'+name
    m=unreal.load_asset(path)
    if m: return m
    m=unreal.AssetToolsHelpers.get_asset_tools().create_asset('M_'+name,ASSETS,unreal.Material,unreal.MaterialFactoryNew())
    m.modify()
    expr=unreal.MaterialEditingLibrary.create_material_expression(m,unreal.MaterialExpressionConstant3Vector)
    expr.set_editor_property('constant',unreal.LinearColor(*color,1))
    unreal.MaterialEditingLibrary.connect_material_property(expr,'',unreal.MaterialProperty.MP_BASE_COLOR)
    unreal.MaterialEditingLibrary.recompile_material(m)
    return m

def mesh_actor(label,mesh,position,scale=(1,1,1),mat=None):
    a=unreal.get_editor_subsystem(unreal.EditorActorSubsystem).spawn_actor_from_class(unreal.StaticMeshActor,unreal.Vector(*position))
    a.set_actor_label(label)
    a.modify(); c=a.static_mesh_component; c.modify()
    c.set_mobility(unreal.ComponentMobility.MOVABLE)
    c.set_static_mesh(mesh)
    c.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
    if mat: c.set_material(0,mat)
    a.set_actor_scale3d(unreal.Vector(*scale))
    return a

def set_pose(side,q,finger=-0.02):
    if side not in BASES: raise ValueError('side must be left or right')
    transforms=frames(q,finger)  # Validate the complete input before making changes.
    existing=actors()
    missing=[PREFIX+side+'_'+link for link in transforms if PREFIX+side+'_'+link not in existing]
    if missing: raise ValueError('Station missing links: '+str(missing))
    for link,t in transforms.items():
        a=existing[PREFIX+side+'_'+link]; a.modify()
        p,r=ue_pose(t); p=[p[i]+BASES[side][i] for i in range(3)]
        a.set_actor_location_and_rotation(unreal.Vector(*p),unreal.Quat(*r).rotator(),False,True)
    return {'side':side,'joints_rad':q,'finger_m':finger,'links':len(transforms)}

def build():
    if any(label.startswith(PREFIX) for label in actors()):
        raise ValueError('Station already exists; use set_pose instead of duplicating it')
    names=list(frames(REST))
    meshes={name:unreal.load_asset(ASSETS+'/SM_YAM_'+name) for name in names}
    if not all(meshes.values()): raise ValueError('Import all nine YAM meshes first')
    white=material('YAM_White',(0.78,0.82,0.86)); dark=material('YAM_Dark',(0.025,0.033,0.045))
    wood=material('YAM_Table',(0.55,0.39,0.22)); red=material('YAM_Stop',(0.8,0.015,0.008))
    for side,base in BASES.items():
        for name in names:
            mesh_actor(PREFIX+side+'_'+name,meshes[name],base,mat=white if name in ('link2','link3') else dark)
        set_pose(side,REST)
    cube=unreal.load_asset('/Engine/BasicShapes/Cube.Cube')
    mesh_actor(PREFIX+'Table',cube,(65,0,73),(1.2,1.8,0.06),wood)
    for x in (15,115):
        for y in (-78,78): mesh_actor(PREFIX+f'Leg_{x}_{y}',cube,(x,y,35),(0.05,0.05,0.7),dark)
    mesh_actor(PREFIX+'CameraMast',cube,(7,0,109),(0.025,0.025,0.66),dark)
    mesh_actor(PREFIX+'OverviewCameraProxy',cube,(9,0,142),(0.06,0.10,0.04),white)
    mesh_actor(PREFIX+'StopButtonProxy',cube,(20,0,79),(0.06,0.06,0.04),red)
    mesh_actor(PREFIX+'ControlComputerProxy',cube,(22,15,79),(0.13,0.13,0.06),dark)
    camera=unreal.get_editor_subsystem(unreal.EditorActorSubsystem).spawn_actor_from_class(unreal.CameraActor,unreal.Vector(9,0,142),unreal.Rotator(-50,0,0))
    camera.set_actor_label(PREFIX+'OverviewCamera_Uncalibrated')
    camera.camera_component.set_editor_property('field_of_view',70.)
    camera.set_actor_scale3d(unreal.Vector(0.08,0.08,0.08))
    key=unreal.get_editor_subsystem(unreal.EditorActorSubsystem).spawn_actor_from_class(unreal.DirectionalLight,unreal.Vector(0,0,250),unreal.Rotator(-55,-35,0))
    key.set_actor_label(PREFIX+'KeyLight')
    key.light_component.set_editor_property('intensity',0.4)
    fill=unreal.get_editor_subsystem(unreal.EditorActorSubsystem).spawn_actor_from_class(unreal.PointLight,unreal.Vector(150,100,200))
    fill.set_actor_label(PREFIX+'FillLight')
    fill.light_component.set_editor_property('intensity',100.)
    fill.light_component.set_editor_property('attenuation_radius',600.)
    return {'arms':2,'arm_dof':6,'links':18,'layout':'estimated','mode':'kinematic editor preview'}

def validate():
    existing=actors(); expected=[PREFIX+s+'_'+n for s in BASES for n in frames(REST)]
    return {'robot_links':sum(n in existing for n in expected),'expected_links':18,
            'missing':[n for n in expected if n not in existing],
            'station_actors':sum(n.startswith(PREFIX) for n in existing)}
