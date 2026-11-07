# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=2,<3", "scipy>=1.14,<2"]
# ///
"""Check generated hull coverage against every official collision-mesh vertex.

This detects missing/scaled/reflected geometry. It does not prove the absence of
excess material in concavities or validate contact friction or dynamics.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
import numpy as np
from scipy.spatial import ConvexHull

FOLDER=Path(__file__).resolve().parents[1]/'Saved/CoPresenceImports/MakerArmPhysics'
VECTOR=re.compile(r'\(X=([-+\deE.]+),Y=([-+\deE.]+),Z=([-+\deE.]+)\)')
IDENTITY='Transform=(Rotation=(X=0.000000,Y=0.000000,Z=0.000000,W=1.000000),Translation=(X=0.000000,Y=0.000000,Z=0.000000),Scale3D=(X=1.000000,Y=1.000000,Z=1.000000))'


def validate(folder=FOLDER,tolerance_cm=.05):
    exported=json.loads((folder/'collision-hulls-text.json').read_text())
    result={}
    for name,texts in exported.items():
        points=np.unique(np.array([[float(x) for x in line.split()[1:]] for line in (folder/(name+'.obj')).read_text().splitlines() if line.startswith('v ')]),axis=0)
        points[:,1]*=-1 # The OBJ importer reflects Y; source OBJ is RH centimetres.
        hull_points=[]
        margin=np.full(len(points),np.inf)
        for text in texts:
            if IDENTITY not in text: raise ValueError('Nonidentity hull transform requires explicit conversion')
            vertices=np.array([[float(x) for x in match] for match in VECTOR.findall(text.split(',IndexData=',1)[0])])
            if len(vertices)<4 or not np.isfinite(vertices).all(): raise ValueError('Invalid hull vertices')
            hull_points.extend(vertices)
            equations=ConvexHull(vertices).equations
            # Bounded batches avoid allocations proportional to entire CAD * hull faces.
            for start in range(0,len(points),2048):
                stop=min(start+2048,len(points))
                outside=np.max(points[start:stop]@equations[:,:3].T+equations[:,3],axis=1)
                margin[start:stop]=np.minimum(margin[start:stop],outside)
        hull_points=np.array(hull_points)
        result[name]={'hulls':len(texts),'unique_source_vertices':len(points),
                      'vertices_outside_tolerance':int(np.sum(margin>tolerance_cm)),
                      'covered_fraction':float(np.mean(margin<=tolerance_cm)),
                      'max_outside_plane_margin_cm':float(max(0,np.max(margin))),
                      'bounds_max_error_cm':float(max(np.max(np.abs(points.min(0)-hull_points.min(0))),np.max(np.abs(points.max(0)-hull_points.max(0)))))}
    report={'tolerance_cm':tolerance_cm,'metric':'vertex coverage by union of convex halfspaces; not a contact accuracy certificate','links':result}
    (folder/'collision-validation.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

def validate_parts(name, folder=FOLDER, tolerance_cm=.05):
    """Offline coverage gate; does not certify UE cooking or concavity fidelity."""
    from maker_arm_model import MODEL
    derived=json.loads((folder/(name+'-part-hulls.json')).read_text())
    if (derived['link']!=name or derived['coordinates']!='UE link-local cm' or
            derived['urdf_sha256']!=hashlib.sha256(MODEL.read_bytes()).hexdigest()):
        raise ValueError('Derived collision provenance or coordinates mismatch')
    if not derived['hulls'] or tolerance_cm <= 0:
        raise ValueError('Missing hulls or invalid tolerance')
    points=np.unique(np.array([[float(x) for x in line.split()[1:]]
        for line in (folder/(name+'.obj')).read_text().splitlines() if line.startswith('v ')]),axis=0)
    points[:,1]*=-1
    covered=np.zeros(len(points),dtype=bool)
    for hull in derived['hulls']:
        vertices=np.asarray(hull['vertices'])
        if len(vertices)<4 or not np.isfinite(vertices).all(): raise ValueError('Invalid hull')
        equations=ConvexHull(vertices).equations
        # Bounding-box rejection preserves the coverage result and bounds cost.
        candidate=(~covered)&np.all((points>=vertices.min(0)-tolerance_cm)&
                                   (points<=vertices.max(0)+tolerance_cm),axis=1)
        if candidate.any():
            covered[candidate]=np.max(points[candidate]@equations[:,:3].T+equations[:,3],axis=1)<=tolerance_cm
    report={'link':name,'hulls':len(derived['hulls']),'unique_source_vertices':len(points),
            'tolerance_cm':tolerance_cm,'vertices_outside_tolerance':int((~covered).sum()),
            'covered_fraction':float(covered.mean()),
            'qualification':'offline vertex coverage only; UE cooking, concavity and contacts unvalidated'}
    (folder/(name+'-part-validation.json')).write_text(json.dumps(report,indent=2)+'\n')
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--part-link',help='Validate offline part hulls instead of exported UE hulls')
    args=parser.parse_args()
    print(json.dumps(validate_parts(args.part_link) if args.part_link else validate(),indent=2))
