import sys,json,time
from pathlib import Path
import numpy as np,trimesh,xml.etree.ElementTree as E
R=Path('/tmp/humantouch-main-integration-1320');sys.path.insert(0,str(R/'Unreal/TheHumanTouch/Tools'))
from maker_arm_model import mounted_frames
from yam_kinematics import origin
root=R/'Unreal/TheHumanTouch/SourceAssets/MakerArm';urdf=E.parse(root/'robot.urdf').getroot()
run=Path('/Users/geo_dennis/Desktop/Projects/geotech-robotics-hackathon/geotech-robotics-hackathon/Unreal/TheHumanTouch/Saved/DreamFleet/20261107-193913-f7d043')
home=json.load(open(run/'T01/action-01.json'))['state'];hf=mounted_frames(home['q_left'],home['gap_left_m'],yaw=np.pi);offset=(np.array(hf['link_007'])@np.array([.03,0,-.118,1]))[:3]-np.array(hf['grasp_center'])[:3,3]
meshes={};bounds={}
for link in urdf.findall('link'):
 parts=[]
 for v in link.findall('visual'):
  el=v.find('geometry/mesh');m=trimesh.load(root/el.get('filename'),process=True);m.apply_scale(.001);m.apply_transform(np.array(origin(v.find('origin'))));parts.append(m)
 if parts:meshes[link.get('name')]=trimesh.util.concatenate(parts)
records=[];started=time.time()
# center plus eight inner/outer frustum samples, 90H/320x176 projection.
directions=np.array([[0,0,-1]]+[[x,y,-1] for x,y in [(.1375,0),(-.1375,0),(0,.25),(0,-.25),(.55,1),(.55,-1),(-.55,1),(-.55,-1)]],float)
for table in sorted(run.glob('T??')):
 states=[(f.name,json.load(open(f))['state']) for f in sorted(table.glob('raw-action-*.json'))]
 states.append(('final',json.load(open(table/'observation.json'))['state']))
 for stage,state in states:
  for side in ('left','right'):
   frames=mounted_frames(state['q_'+side],state['gap_'+side+'_m'],yaw=np.pi);p=np.array(frames['grasp_center'])[:3,3]+offset
   distances=np.full(len(directions),np.inf);hitlinks=['']*len(directions);near=[];inside=[]
   for name,m in meshes.items():
    if name not in frames:continue
    inv=np.linalg.inv(np.array(frames[name]));local=(inv@np.r_[p,1])[:3];dirs=directions@inv[:3,:3].T
    # Only classify containment when bounding box includes the point.
    if np.all(local>=m.bounds[0]) and np.all(local<=m.bounds[1]):
     inside.append({'link':name,'closed':bool(m.is_watertight),'contains':bool(m.contains([local])[0]) if m.is_watertight else None})
    hits,ray,_=m.ray.intersects_location(np.tile(local,(len(dirs),1)),dirs)
    for h,ri in zip(hits,ray):
     dist=float(np.linalg.norm(h-local));table_dist=(p[2]+.01)*np.linalg.norm(directions[ri])
     if dist<distances[ri] and dist<table_dist:distances[ri]=dist;hitlinks[ri]=name
   records.append({'table':table.name,'stage':stage,'side':side,'position_base_m':p.tolist(),'below_table':bool(p[2]<=-.01),'point_candidates':inside,'center_hit_cm':None if not np.isfinite(distances[0]) else float(distances[0]*100),'center_hit_link':hitlinks[0],'inner_rays_blocked':int(np.isfinite(distances[:5]).sum()),'all_rays_blocked':int(np.isfinite(distances).sum())})
 print(table.name,'processed',len(records),'seconds',round(time.time()-started,1),flush=True)
summary={'offset_world_m':offset.tolist(),'count':len(records),'below_table':sum(r['below_table'] for r in records),'confirmed_inside':sum(any(x['contains'] for x in r['point_candidates']) for r in records),'unresolved_open_bbox':sum(any(x['contains'] is None for x in r['point_candidates']) for r in records),'center_blocked':sum(r['center_hit_cm'] is not None for r in records),'any_inner_blocked':sum(r['inner_rays_blocked']>0 for r in records)}
json.dump({'summary':summary,'records':records},open('/tmp/tcp-stabilized-camera-audit.json','w'),indent=2);print(summary)
