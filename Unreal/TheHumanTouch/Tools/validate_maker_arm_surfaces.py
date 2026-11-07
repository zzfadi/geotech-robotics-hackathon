# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=2,<3", "scipy>=1.14,<2", "trimesh>=4,<5", "rtree>=1,<2"]
# ///
"""Sample collision-hull surfaces for excess material outside original CAD solids.

This complements vertex coverage, but is not a continuous Hausdorff bound,
engine cooking test, performance benchmark or hardware contact validation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import trimesh
from maker_arm_model import MODEL, model, numbers, origin
from maker_arm_physics import description

FOLDER=MODEL.parents[2]/'Saved/CoPresenceImports/MakerArmPhysics'


def outside_distances(source, points):
    """Exact nearest triangle distances, zero for points inside the CAD solid."""
    distances=[]
    for start in range(0,len(points),1024):
        chunk=points[start:start+1024]
        _, distance, _=trimesh.proximity.closest_point(source,chunk)
        # Points on a boundary are already zero; skip ambiguous boundary rays.
        candidates=distance>1e-9
        interior=np.zeros(len(chunk),dtype=bool)
        if candidates.any(): interior[candidates]=source.contains(chunk[candidates])
        distance[interior]=0
        distances.extend(distance)
    return np.asarray(distances)


def validate(name, samples_per_hull=64, tolerance_mm=.5):
    description() # Check all source hashes.
    if samples_per_hull<1 or tolerance_mm<=0: raise ValueError('Invalid sampling settings')
    derived=json.loads((FOLDER/(name+'-part-hulls.json')).read_text())
    if derived['coordinates']!='UE link-local cm' or derived['urdf_sha256']!=hashlib.sha256(MODEL.read_bytes()).hexdigest():
        raise ValueError('Provenance or coordinates mismatch')
    link=next(l for l in model().findall('link') if l.get('name')==name)
    report=[]
    for collision in link.findall('collision'):
        mesh=collision.find('geometry/mesh');filename=mesh.get('filename')
        source=trimesh.load_mesh(MODEL.parent/filename,process=True)
        source.vertices*=np.array(numbers(mesh.get('scale','1 1 1')))*1000
        if not source.is_watertight:
            # Same numerical seam weld as decomposition, converted to mm.
            source.merge_vertices(digits_vertex=5)
            source.update_faces(source.nondegenerate_faces())
            source.remove_unreferenced_vertices()
        if not source.is_watertight: raise ValueError('Cannot classify solid interior: '+filename)
        source.fix_normals()
        transform=np.asarray(origin(collision.find('origin')))
        selected=[h for h in derived['hulls'] if h['source']==filename]
        if not selected: raise ValueError('Missing CAD part: '+filename)
        samples=[]
        for index,h in enumerate(selected):
            # UE link cm -> source part mm, undoing handedness and URDF origin.
            points=np.asarray(h['vertices'])*np.array([.01,-.01,.01])
            points=(points-transform[:3,3])@transform[:3,:3]*1000
            hull=trimesh.Trimesh(points,np.asarray(h['faces'])[:,[0,2,1]],process=False)
            random_points,_=trimesh.sample.sample_surface(hull,samples_per_hull,seed=index)
            samples.extend((points,hull.triangles_center,random_points))
        samples=np.concatenate(samples)
        distance=outside_distances(source,samples)
        worst=int(np.argmax(distance))
        row={'part':filename,'hulls':len(selected),'surface_samples':len(samples),
             'max_sampled_excess_mm':float(distance[worst]),
             'samples_outside_tolerance':int((distance>tolerance_mm).sum()),
             'worst_source_part_point_mm':samples[worst].tolist()}
        report.append(row);print(json.dumps(row),flush=True)
    result={'link':name,'tolerance_mm':tolerance_mm,'random_samples_per_hull':samples_per_hull,
            'sampling':'all hull vertices, triangle centroids and seeded area-weighted samples',
            'qualification':'sampled per-part exterior error; not continuous or physical validation',
            'derived_sha256':hashlib.sha256((FOLDER/(name+'-part-hulls.json')).read_bytes()).hexdigest(),
            'parts':report}
    (FOLDER/(name+'-surface-validation.json')).write_text(json.dumps(result,indent=2)+'\n')
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('link');parser.add_argument('--samples-per-hull',type=int,default=64)
    args=parser.parse_args();validate(args.link,args.samples_per_hull)
