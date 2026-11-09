import bpy, math, os
from mathutils import Vector
P=os.path.dirname(os.path.abspath(__file__))
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
def mat(name,color,metal=0,rough=.3):
 m=bpy.data.materials.new(name);m.diffuse_color=(*color,1);m.use_nodes=True
 n=next(n for n in m.node_tree.nodes if n.type=='BSDF_PRINCIPLED');n.inputs['Base Color'].default_value=(*color,1);n.inputs['Metallic'].default_value=metal;n.inputs['Roughness'].default_value=rough
 return m
silver=mat('Brushed aluminum',(.65,.69,.72),.85,.27); dark=mat('Opening shadow',(.012,.016,.02),.4,.3)
label=mat('Orange printed aluminum',(.9,.15,.02),.35,.3)
n=next(n for n in label.node_tree.nodes if n.type=='BSDF_PRINCIPLED');t=label.node_tree.nodes.new('ShaderNodeTexImage');t.image=bpy.data.images.load(P+'/label.png');label.node_tree.links.new(t.outputs['Color'],n.inputs['Base Color'])
def lathe(name,profile,material):
 N=160;vs=[];fs=[]
 for r,z in profile:
  for j in range(N+1):
   a=2*math.pi*j/N+math.pi/2;vs.append((r*math.cos(a),r*math.sin(a),z))
 for i in range(len(profile)-1):
  for j in range(N):
   a=i*(N+1)+j;fs.append((a,a+1,a+N+2,a+N+1))
 me=bpy.data.meshes.new(name);me.from_pydata(vs,[],fs);me.update();o=bpy.data.objects.new(name,me);bpy.context.collection.objects.link(o);o.data.materials.append(material)
 uv=me.uv_layers.new()
 for poly in me.polygons:
  poly.use_smooth=True
  for li in poly.loop_indices:
   idx=me.loops[li].vertex_index;i,j=divmod(idx,N+1);uv.data[li].uv=(j/N,profile[i][1]/.122)
 return o
body=lathe('Can body • 355 mL',[(.0,.004),(.024,.004),(.027,.001),(.029,.001),(.030,.004),(.0325,.011),(.033,.018),(.033,.100),(.0325,.106),(.029,.115),(.0285,.119)],label)
lathe('Bottom rolled rim',[(.027,.002),(.027,0),(.0285,0),(.0295,.001),(.0295,.003),(.028,.004)],silver)
lathe('Top rolled seam',[(.028,.118),(.0292,.118),(.0298,.119),(.0298,.1208),(.029,.122),(.028,.122),(.0275,.121),(.0275,.119)],silver)
lathe('Recessed lid',[(0,.1192),(.024,.1192),(.0255,.1188),(.0275,.120),(.028,.121)],silver)
def uvball(name,loc,scale,material):
 bpy.ops.mesh.primitive_uv_sphere_add(segments=48,ring_count=20,location=loc);o=bpy.context.object;o.name=name;o.scale=scale;o.data.materials.append(material)
 for f in o.data.polygons:f.use_smooth=True
 return o
uvball('Opened drinking aperture',(0,-.012,.1194),(.010,.007,.00035),dark)
# tab is a flattened metal loop with real center opening
N=80;vs=[];fs=[]
for z in [.1200,.1212]:
 for rx,ry in [(.008,.014),(.0043,.0075)]:
  for j in range(N):
   a=2*math.pi*j/N;vs.append((rx*math.cos(a),.003+ry*math.sin(a),z))
for j in range(N):
 k=(j+1)%N
 for a,b in [(0,N),(2*N,3*N),(0,2*N),(N,3*N)]:fs.append((a+j,a+k,b+k,b+j))
me=bpy.data.meshes.new('Tab mesh');me.from_pydata(vs,[],fs);me.update();o=bpy.data.objects.new('Pull tab',me);bpy.context.collection.objects.link(o);o.data.materials.append(silver)
b= o.modifiers.new('Rounded stamped edge','BEVEL');b.width=.0006;b.segments=3
uvball('Tab rivet',(0,.010,.1215),(.002,.002,.0008),silver)
# export only can geometry before studio
bpy.ops.object.select_all(action='SELECT')
bpy.ops.export_scene.gltf(filepath=P+'/free_wave_can.glb',use_selection=True)
ground=mat('Studio slate',(.075,.09,.11),0,.65)
bpy.ops.mesh.primitive_plane_add(size=200,location=(0,0,-.0008));bpy.context.object.name='Studio floor';bpy.context.object.data.materials.append(ground)
bpy.ops.object.camera_add(location=(.155,-.30,.18));cam=bpy.context.object;cam.rotation_euler=(Vector((0,0,.061))-cam.location).to_track_quat('-Z','Y').to_euler();cam.data.type='ORTHO';cam.data.ortho_scale=.175;bpy.context.scene.camera=cam
for name,loc,power,size in [('Key',(.08,-.12,.25),12,.15),('Fill',(-.13,-.04,.14),7,.13),('Rim',(.04,.12,.21),15,.10)]:
 bpy.ops.object.light_add(type='AREA',location=loc);o=bpy.context.object;o.name=name;o.data.energy=power;o.data.shape='DISK';o.data.size=size;o.rotation_euler=(Vector((0,0,.06))-o.location).to_track_quat('-Z','Y').to_euler()
s=bpy.context.scene;s.render.engine='CYCLES';s.cycles.samples=32;s.render.resolution_x=1000;s.render.resolution_y=1000;s.render.resolution_percentage=100;s.world.color=(.22,.22,.22)
s.unit_settings.system='METRIC';s.render.filepath=P+'/preview.png'
for a in bpy.context.screen.areas:
 if a.type=='VIEW_3D':a.spaces.active.region_3d.view_perspective='CAMERA'
bpy.ops.file.pack_all();bpy.ops.wm.save_as_mainfile(filepath=P+'/free_wave_can.blend');bpy.ops.render.render(write_still=True)
print('SAVED',P)
