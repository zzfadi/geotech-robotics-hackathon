import sys,json,xml.etree.ElementTree as E
import numpy as np,trimesh
from pathlib import Path
R=Path('/tmp/humantouch-main-integration-1320');sys.path.insert(0,str(R/'Unreal/TheHumanTouch/Tools'))
from maker_arm_model import mounted_frames
from yam_kinematics import origin
root=R/'Unreal/TheHumanTouch/SourceAssets/MakerArm';urdf=E.parse(root/'robot.urdf').getroot()
q0=[-8.341015791761527e-08,-1.3267562769368857,1.5947274948757348,-1.4680311465744582,1.6235197063895467e-08,-8.180590968960631e-08]
q1=[.2259192208,-2.5290293927,2.7282684562,-1.018995541,-.0452610609,.2043888269]
results={}
for name,q in [('idle',q0),('moved',q1)]:
 frames=mounted_frames(q,.1);point=(np.array(frames['link_007'])@np.array([.03,0,-.118,1]))[:3];rows=[]
 for link in urdf.findall('link'):
  if link.get('name') not in frames:continue
  for visual in link.findall('visual'):
   el=visual.find('geometry/mesh');mesh=trimesh.load(root/el.get('filename'),process=True);mesh.apply_scale([float(v) for v in el.get('scale','1 1 1').split()]);mesh.apply_transform(np.array(frames[link.get('name')])@np.array(origin(visual.find('origin'))))
   _,dist,_=trimesh.proximity.closest_point(mesh,[point]);inside=bool(mesh.contains([point])[0]) if mesh.is_watertight else None
   rows.append(dict(link=link.get('name'),mesh=el.get('filename'),surface_distance_mm=float(dist[0]*1000),watertight=mesh.is_watertight,inside=inside,inside_aabb=bool(np.all(point>=mesh.bounds[0]) and np.all(point<=mesh.bounds[1]))))
 results[name]={'point_base_m':point.tolist(),'nearest':sorted(rows,key=lambda r:r['surface_distance_mm'])[:6],'inside':[r for r in rows if r['inside']] ,'nonwatertight':[r for r in rows if not r['watertight']]}
open('/tmp/compact-camera-clearance.json','w').write(json.dumps(results,indent=2));print(json.dumps(results,indent=2))
