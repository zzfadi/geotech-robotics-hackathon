"""Offline markerless camera/arm fit with reserved pose validation.

Natural feature coordinates on the rigid wrist are unknowns. SDK motor FK is
the provisional motion reference. This fits data; it does not authorize motion.
"""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from scipy.optimize import least_squares

from calibration_geometry import raw_motor_fk
from table_reference import homography,project


def transform(vector):
    T=np.eye(4);T[:3,:3]=cv2.Rodrigues(np.asarray(vector[:3]))[0];T[:3,3]=vector[3:6]
    return T


def vector(T):
    return np.r_[cv2.Rodrigues(T[:3,:3])[0].ravel(),T[:3,3]]


def predict(X,B,points,focal,center,k1=0.):
    world=points@B[:3,:3].T+B[:3,3]
    cam=world@X[:3,:3].T+X[:3,3]
    z=np.maximum(cam[:,2],.01)
    xy=cam[:,:2]/z[:,None]
    radial=1+k1*(xy*xy).sum(axis=1)
    return xy*radial[:,None]*focal+center


def fit(observations, initial_X, initial_points, feature_ids, image_size, focal):
    center=np.array(image_size)/2
    fit_rows=[o for o in observations if o['role']=='fit']
    validation_rows=[o for o in observations if o['role']=='validation']
    if len(fit_rows)<6 or len(validation_rows)<2 or len(feature_ids)<8:
        raise ValueError('Need >=6 fitting poses, >=2 reserved validation poses and >=8 common features')
    def arrays(rows):
        Bs=[];pixels=[]
        for row in rows:
            features={f['id']:f['image_px'] for f in row['marker']['features']}
            Bs.append(raw_motor_fk(row['position_deg']))
            pixels.append(np.array([features[i] for i in feature_ids]))
        return Bs,pixels
    Bs,pixels=arrays(fit_rows);val_Bs,val_pixels=arrays(validation_rows)
    def unpack(p):return transform(p[:6]),p[8:].reshape(-1,3),np.exp(p[6]),p[7]
    def residual(p):
        X,points,f,k=unpack(p)
        return np.concatenate([(predict(X,B,points,f,center,k)-pixel).ravel() for B,pixel in zip(Bs,pixels)])
    seed=np.r_[vector(initial_X),np.log(focal),0.,initial_points.ravel()]
    lower=np.r_[[-6.3]*3,[-3]*3,np.log(250.),-.7, np.full(initial_points.size,-.3)]
    upper=np.r_[[6.3]*3,[3]*3,np.log(2500.),.7, np.full(initial_points.size,.3)]
    seed=np.clip(seed,lower+1e-6,upper-1e-6)
    result=least_squares(residual,seed,bounds=(lower,upper),loss='soft_l1',f_scale=1.,
                         max_nfev=1200,x_scale='jac',ftol=1e-10,xtol=1e-10,gtol=1e-10)
    X,points,f,k=unpack(result.x)
    fitting=residual(result.x).reshape(-1,2)
    validation=np.concatenate([predict(X,B,points,f,center,k)-pixel for B,pixel in zip(val_Bs,val_pixels)])
    singular=np.linalg.svd(result.jac,compute_uv=False)
    all_Bs=Bs+val_Bs
    min_depth=min(float(((points@B[:3,:3].T+B[:3,3])@X[:3,:3].T+X[:3,3])[:,2].min()) for B in all_Bs)
    errors=np.linalg.norm(validation,axis=1)
    return {'T_camera_base':X.tolist(),'feature_ids':feature_ids,'feature_xyz_wrist_m':points.tolist(),
            'image_size_px':list(image_size),'focal_px':float(f),'principal_point_px':center.tolist(),
            'radial_k1':float(k),'fit_rms_px':float(np.sqrt(np.mean(np.sum(fitting*fitting,axis=1)))),
            'validation_rms_px':float(np.sqrt(np.mean(errors**2))),
            'validation_max_px':float(errors.max()),'jacobian_singular_values':singular.tolist(),
            'min_depth_m':min_depth,'optimizer_success':bool(result.success),
            'motion_ready':False,
            'limitations':['Fixed central principal-point and equal focal-length assumptions',
                'SDK motor geometry is provisional; absolute hardware alignment still requires verification',
                'No pickup permission from an image fit alone; require metric and workspace validation']}


def common_features(observations,limit=16):
    common=set.intersection(*[{f['id'] for f in o['marker']['features']} for o in observations])
    first={f['id']:np.array(f['image_px']) for f in observations[0]['marker']['features']}
    if not common:return []
    selected=[min(common)]
    while len(selected)<min(limit,len(common)):
        selected.append(max(common-set(selected),key=lambda i:min(np.linalg.norm(first[i]-first[j]) for j in selected)))
    return selected


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('poses',type=Path);p.add_argument('--table-reference',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists():p.error('Use a new output file')
    run=json.loads(a.poses.read_text());obs=run['observations']
    if len(obs)<8:p.error('Not enough observations; incomplete collection cannot produce alignment')
    reference=json.loads(a.table_reference.read_text())
    size=reference['image_size_px'];center=np.array(size)/2
    ids=common_features(obs)
    if len(ids)<8:p.error('Too few features survive all fitting and validation poses')
    card=np.array([[0,0,0],[.063,0,0],[.063,.088,0],[0,.088,0]],np.float64)
    image=np.array(reference['corners_px'],np.float64)
    plane=homography(image,card[:,:2])
    base_xy=project(plane,[reference['base_pixel_initialization_only']])[0]
    table_base=np.eye(4);table_base[:3,:3]=[[0,-1,0],[-1,0,0],[0,0,-1]]
    table_base[:3,3]=[*base_xy,-.03]
    first=obs[0];B=raw_motor_fk(first['position_deg'])
    pixel={f['id']:np.array(f['image_px']) for f in first['marker']['features']}
    candidates=[]
    for focal in [400.,600.,900.,1300.]:
        K=np.array([[focal,0,center[0]],[0,focal,center[1]],[0,0,1.]])
        success,r,t=cv2.solvePnP(card,image,K,np.zeros(5),flags=cv2.SOLVEPNP_IPPE)
        if not success:continue
        cam_table=transform(np.r_[r.ravel(),t.ravel()]);X=cam_table@table_base
        wrist_cam=np.linalg.inv(X@B)
        points=[]
        for i in ids:
            ray=wrist_cam[:3,:3]@np.linalg.solve(K,np.r_[pixel[i],1.])
            origin=wrist_cam[:3,3]
            distance=(-.06-origin[2])/ray[2]
            points.append(np.clip(origin+distance*ray,-.25,.25))
        candidate=fit(obs,X,np.array(points),ids,size,focal)
        candidate['initial_focal_px']=focal;candidates.append(candidate)
        print('FIT',focal,candidate['fit_rms_px'],candidate['validation_rms_px'],flush=True)
    candidates.sort(key=lambda c:c['fit_rms_px'])
    result={'source_poses':str(a.poses),'reference':reference,'candidates':candidates,
            'best':candidates[0] if candidates else None,'motion_ready':False}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')


if __name__=='__main__':main()
