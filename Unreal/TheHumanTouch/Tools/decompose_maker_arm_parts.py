# /// script
# requires-python = ">=3.11"
# dependencies = ["coacd==1.0.14", "trimesh>=4,<5", "scipy>=1.14,<2"]
# ///
"""Decompose original CAD collision parts independently, preserving small solids.

Cache by source bytes and algorithm settings. Output is in UE link-local cm,
including Y reflection. This is derived geometry, never a source CAD edit.
"""
import argparse
import hashlib
import json
from pathlib import Path
import coacd
import numpy as np
import trimesh
from scipy.spatial import cKDTree
from maker_arm_model import MODEL, model, origin, numbers
from maker_arm_physics import description

VERSION='coacd-1.0.14'
FOLDER=MODEL.parents[2]/'Saved/CoPresenceImports/MakerArmPhysics'


def decompose_link(name, threshold_m=.00025, merge=False):
    description() # Verify every pinned source hash before deriving geometry.
    if not 0 < threshold_m <= .001: raise ValueError('Expected positive threshold at most 1 mm')
    link=next((l for l in model().findall('link') if l.get('name')==name),None)
    if link is None or not link.findall('collision'): raise ValueError('Unknown collision link')
    cache=FOLDER/'PartCache';cache.mkdir(parents=True,exist_ok=True)
    output=[]; report=[]
    for collision in link.findall('collision'):
        mesh=collision.find('geometry/mesh')
        path=MODEL.parent/mesh.get('filename')
        scale=numbers(mesh.get('scale','1 1 1'))
        settings={'version':VERSION,'threshold_m':threshold_m,'scale':scale,
                  'seed':0,'resolution':4000,'preprocess_resolution':100,
                  'decimate':False,'merge':merge,'real_metric':True}
        key=hashlib.sha256(path.read_bytes()+json.dumps(settings,sort_keys=True).encode()).hexdigest()
        entry=cache/(key+'.json')
        if entry.exists():
            result=json.loads(entry.read_text())
        else:
            shape=trimesh.load_mesh(path,process=True)
            shape.vertices*=np.array(scale)
            repair=None
            if not shape.is_watertight:
                before=shape.vertices.copy()
                shape.merge_vertices(digits_vertex=8) # 10 nm grid in metre coordinates.
                shape.update_faces(shape.nondegenerate_faces())
                shape.remove_unreferenced_vertices()
                deviation=float(cKDTree(shape.vertices).query(before)[0].max())
                if not shape.is_watertight or deviation>2e-8:
                    raise ValueError('CAD part needs repair beyond numerical seam welding: '+path.name)
                repair={'kind':'numerical seam welding and degenerate-face removal',
                        'grid_m':1e-8,'max_vertex_displacement_m':deviation}

            # Repair winding only; preserves vertex positions and the represented surface.
            shape.fix_normals()
            convex=shape.convex_hull
            if abs(convex.volume-shape.volume) <= 1e-7*max(convex.volume,1e-20):
                pieces=[(convex.vertices,convex.faces)]
                method='exact convex hull of convex CAD solid'
            else:
                coacd.set_log_level('warn')
                pieces=coacd.run_coacd(coacd.Mesh(shape.vertices,shape.faces),
                    threshold=threshold_m,real_metric=True,preprocess_mode='off',
                    resolution=4000,mcts_iterations=150,merge=merge,decimate=False,seed=0)
                method=VERSION
            if not pieces: raise ValueError('No hulls for '+path.name)
            result={'source':mesh.get('filename'),'settings':settings,'method':method,'repair':repair,
                    'hulls':[{'vertices':v.tolist(),'faces':f.tolist()} for v,f in pieces]}
            entry.write_text(json.dumps(result)+'\n')
        transform=np.array(origin(collision.find('origin')))
        for hull in result['hulls']:
            points=np.array(hull['vertices'])@transform[:3,:3].T+transform[:3,3]
            points*=np.array([100,-100,100])
            # Reflecting the coordinates reverses triangle orientation.
            faces=np.array(hull['faces'])[:,[0,2,1]]
            output.append({'vertices':points.tolist(),'faces':faces.tolist(),
                           'source':mesh.get('filename')})
        report.append({'source':mesh.get('filename'),'hulls':len(result['hulls']),
                       'method':result['method'],'cache_key':key,'repair':result.get('repair')})
        print(name,path.name,len(result['hulls']),'hulls',flush=True)
    result={'link':name,'coordinates':'UE link-local cm','threshold_m':threshold_m,'merge':merge,
            'urdf_sha256':hashlib.sha256(MODEL.read_bytes()).hexdigest(),
            'parts':report,'hulls':output,'status':'requires coverage and contact checks'}
    (FOLDER/(name+'-part-hulls.json')).write_text(json.dumps(result)+'\n')
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('link');parser.add_argument('--threshold-m',type=float,default=.00025)
    parser.add_argument('--merge',action='store_true',help='Merge hulls under the same CoACD concavity threshold')
    args=parser.parse_args()
    result=decompose_link(args.link,args.threshold_m,merge=args.merge)
    print('Prepared',len(result['hulls']),'hulls for',args.link)
