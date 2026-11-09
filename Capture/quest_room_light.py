# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["numpy>=1.26,<3", "scipy>=1.12,<2", "opencv-python-headless>=4.9,<5", "psutil>=6"]
# ///
"""Measure a low-frequency room light probe from the Quest's left passthrough camera."""
import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import cv2
import numpy as np
from scipy.spatial.transform import Rotation
from scipy.spatial import cKDTree

from quest_relock import ROOT, atomic, forward, read_frame
from plate_vision import Hold

DIR=ROOT/'Unreal/TheHumanTouch/Saved/Capture/RoomLight'
LUMA=np.array([.2126,.7152,.0722])
HEIGHT,WIDTH=9,12

def editor(expression):
    tool=ROOT/'Unreal/TheHumanTouch/Tools/copresence_mcp.py'
    cmd=['uv','run','--script',str(tool),'exec-python','8041',expression]
    r=subprocess.run(cmd,env={**os.environ,'COPRESENCE_UNREAL_MCP_URL':'http://127.0.0.1:8041/mcp'},capture_output=True,text=True,timeout=30)
    if r.returncode or 'ERROR (' in r.stdout:
        raise Hold((r.stdout+r.stderr)[-900:])
    return r.stdout

def presentation(expression):
    return editor("import unreal,sys; from pathlib import Path; sys.path.insert(0,str(Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()))/'Tools')); import station_presentation as sp; "+expression)

def normalize(v):
    return v/np.maximum(np.linalg.norm(v,axis=-1,keepdims=True),1e-9)

def srgb_linear(value):
    return np.where(value<=.04045,value/12.92,((value+.055)/1.055)**2.4)

def tiles(image):
    # Only directional averages survive the exposure. Keep fine room imagery in memory.
    small=cv2.resize(image[:,:,::-1],(WIDTH*8,HEIGHT*8),interpolation=cv2.INTER_AREA)/255.
    return srgb_linear(small).reshape(HEIGHT,8,WIDTH,8,3).mean(axis=(1,3))

def camera_rays(photo,crop):
    w,h=1280,960
    fx,fy,cx,cy,skew=photo['intrinsics']
    left,top,right,bottom=map(float,crop.split())
    sw,sh=right-left,bottom-top
    # The Android stream uses a central aspect crop if the sensor region is square.
    nw,nh=min(sw,sh*w/h),min(sh,sw*h/w)
    left+=(sw-nw)/2; top+=(sh-nh)/2
    u=left+(np.arange(WIDTH)+.5)*nw/WIDTH; v=top+(np.arange(HEIGHT)+.5)*nh/HEIGHT
    u,v=np.meshgrid(u,v); y=(v-cy)/fy; x=(u-cx-skew*y)/fx
    camera=np.stack([x,y,np.ones_like(x)],axis=-1)
    ray=Rotation.from_quat(photo['sensor_to_camera']).inv().apply(camera.reshape(-1,3))
    origin=np.array(photo['camera_position'])
    # Quest OpenXR: right, up, back. Unreal head: forward, right, up.
    return normalize(np.column_stack([-ray[:,2],ray[:,0],ray[:,1]])), np.array([-origin[2],origin[0],origin[1]])*100

def stationary_pose(history,start,end):
    seq=[r for r in history if start-.09<=r[0]<=end+.10]
    if len(seq)<2 or seq[0][0]>start+.04 or seq[-1][0]<end-.04 or end-start>1.3 or any(b[0]-a[0]>.16 for a,b in zip(seq,seq[1:])):
        raise Hold('waiting for synchronized headset tracking')
    center=seq[len(seq)//2]
    basis=np.asarray(center[2])
    for r in seq:
        cosine=(np.trace(basis@np.asarray(r[2]).T)-1)*.5
        if np.arccos(np.clip(cosine,-1,1))>np.radians(7) or np.linalg.norm(np.subtract(r[1],center[1]))>5 or r[3:]!=center[3:]:
            raise Hold('pause briefly at this viewing angle')
    return center

def acquire(serial, seconds):
    presentation(f"sp.begin_scan({seconds+10})"); seen=set(); heading=set(); samples=[]
    with forward(serial,open_camera=True) as (_,port):
        until=time.monotonic()+seconds
        while time.monotonic()<until:
            try:
                start=time.time(); im,meta,stamp=read_frame(port,photometry=True); end=time.time()
                if stamp in seen:continue
                seen.add(stamp);time.sleep(.10)
                pose=stationary_pose(json.loads((DIR/'pose.json').read_text()),start,end)
                ph=meta['photometry']
                rays,offset=camera_rays(ph,meta['crop'])
                angles=np.asarray(pose[2])[0]
                sector=(round(math.atan2(angles[1],angles[0])*6/math.pi),round(math.asin(np.clip(angles[2],-1,1))*6/math.pi))
                sample={'head':pose[1],'axes':pose[2],'room':pose[3:],'tiles':tiles(im).tolist(),'exposure':ph,'crop':meta['crop']}
                if samples and sample['room']!=samples[0]['room']:raise RuntimeError('Room was recalibrated. Start a fresh scan.')
                samples.append(sample)
                if sector not in heading:
                    heading.add(sector);print(f'{len(heading)} distinct head directions',flush=True)
            except (Hold, OSError, KeyError) as exc:
                if not samples:print(str(exc),flush=True)
            time.sleep(.12)
    if not samples:raise Hold('no valid room exposures')
    return samples

def sphere(n=192):
    i=np.arange(n)+.5; z=1-2*i/n; a=i*(np.pi*(3-np.sqrt(5))); rr=np.sqrt(1-z*z)
    return np.stack([rr*np.cos(a),rr*np.sin(a),z],axis=1)

def fit(samples,bench):
    dirs=sphere(); tree=cKDTree(dirs)
    colors=[[] for _ in dirs]; anchor=[]; clipping=[]
    exposure=np.array([r['exposure']['exposure_ns']*r['exposure']['iso']/r['exposure'].get('aperture',1.)**2 for r in samples])
    if not np.isfinite(exposure).all() or min(exposure)<=0: raise Hold('invalid exposure metadata')
    reference=np.median(exposure)
    n=np.array(bench['across']); across=n[:2]; side=np.array([-across[1],across[0]])
    center=np.array(bench['top_cm'])
    for r,e in zip(samples,exposure):
        rays,offset=camera_rays(r['exposure'],r['crop']); axes=np.array(r['axes'])
        camera=np.array(r['head'])+offset@axes
        world=rays@axes
        rgb=np.reshape(r['tiles'],(-1,3))
        indexes=tree.query(world)[1]
        for j in np.unique(indexes):
            colors[j].append(np.median(rgb[indexes==j],axis=0)*reference/e)
        # The real and virtual tabletop share a measured region away from rims and arm bases.
        z=world[:,2]; t=(center[2]-camera[2])/np.where(np.abs(z)>.001,z,np.nan)
        hit=camera+t[:,None]*world
        rel=hit[:,:2]-center[:2]
        good=(t>10)&(t<180)&(z<-.16)&(np.abs(rel@across)<28)&(np.abs(rel@side)<105)
        for base in bench['bases_cm']:good &= np.linalg.norm(hit[:,:2]-np.asarray(base)[:2],axis=1)>20
        good &= (rgb.max(1)<.94)&(rgb.max(1)>.007)
        if good.sum()>=3:
            anchor.append(np.median(rgb[good],axis=0))
        clipping.append(float(np.mean(rgb.max(1)>.97)))
    used=np.array([bool(c) for c in colors]); support=dirs[used]
    if len(support)<6 or not anchor:raise Hold('look at the clear real tabletop for a calibration frame')
    obs=np.array([np.median(c,axis=0) for c in colors if c])
    luma=obs@LUMA
    ambient=np.maximum(np.median(obs,axis=0),.003)
    coverage=used.mean()
    top=(support[:,2]>.20)
    full=coverage>=.54 and np.count_nonzero(top)>=24 and len(set(np.floor((np.arctan2(support[top,1],support[top,0])+np.pi)*2/np.pi).astype(int)%4))>=3
    # Fit a broad key from the positive upper-hemisphere luminance excess only when observed.
    weights=np.maximum(luma-np.quantile(luma,.70),0)*top
    moment=np.sum(weights[:,None]*support,axis=0)
    coherence=np.linalg.norm(moment)/max(weights.sum(),1e-8)
    key_ok=bool(full and coherence>.42 and np.linalg.norm(moment)>1e-6)
    direction=normalize(moment) if key_ok else np.array([0.,0.,1.])
    key=np.average(obs,axis=0,weights=weights) if key_ok else ambient
    # Reserve the directional excess for the shadow-casting key, avoiding duplicate energy.
    sky=obs.copy()
    if key_ok:
        sky-=np.minimum(sky,np.maximum(key-ambient,0)[None,:]*np.maximum(0,support@direction)[:,None]**12)
    return dict(version=1,coverage=round(float(coverage),4),qualified_key=key_ok,
        direction=direction.tolist(),key_color=(key/max(key)).tolist(),
        key_to_fill=float(np.clip((key@LUMA)/(ambient@LUMA),1,12)),angular_spread_deg=float(np.degrees(np.arccos(np.clip(coherence,0,1)))),
        observed=support.tolist(),radiance=sky.clip(.0001).tolist(),ambient=ambient.tolist(),table_rgb=np.median(anchor,axis=0).tolist(),
        matched_table_views=len(anchor),mean_clipped_tile_fraction=float(np.mean(clipping)),exposures=len(samples))

def write_environment(profile,path):
    # UE's long-lat importer swaps Y/Z to map image Up onto UE world +Z.
    w,h=256,128
    lon=(2*(np.arange(w)+.5)/w-1)*np.pi; polar=(np.arange(h)+.5)/h*np.pi
    lon,polar=np.meshgrid(lon,polar)
    rays=np.stack([np.sin(lon)*np.sin(polar),-np.cos(lon)*np.sin(polar),np.cos(polar)],axis=-1)
    dirs=np.array(profile['observed']);rgb=np.array(profile['radiance']);ambient=np.array(profile['ambient'])
    # Unobserved directions fall back to diffuse fill. Broad filtering retains light lobes without identifiable imagery.
    weights=np.exp(14*(rays.reshape(-1,3)@dirs.T-1))
    env=(weights@rgb+.45*ambient)/(weights.sum(1)[:,None]+.45)
    env/=max(ambient@LUMA,.001)
    if not cv2.imwrite(str(path),env.reshape(h,w,3)[:,:,::-1].astype(np.float32)):raise OSError('HDR probe write failed')

def overview_table(path,bench):
    bgr=cv2.imread(str(path))
    if bgr is None:raise Hold("virtual reference frame unavailable")
    h,w=bgr.shape[:2];c=bench["overview"]
    u,v=np.meshgrid(np.arange(w)+.5,np.arange(h)+.5)
    f=w/(2*math.tan(math.radians(c["fov"]/2)))
    rays=np.stack([np.ones_like(u),(u-w/2)/f,(h/2-v)/f],axis=-1)@np.array(c["axes"])
    center=np.array(bench["top_cm"]);pos=np.array(c["pos"])
    hit=pos+(center[2]-pos[2])/np.where(np.abs(rays[:,:,2:])>.001,rays[:,:,2:],np.nan)*rays
    rel=hit[:,:,:2]-center[:2];axis=np.array(bench["across"])[:2];right=np.array([-axis[1],axis[0]])
    good=(rays[:,:,2]<-.16)&(np.abs(rel@axis)<28)&(np.abs(rel@right)<105)
    for base in bench["bases_cm"]:good &= np.linalg.norm(hit[:,:,:2]-np.array(base)[:2],axis=2)>20
    if good.sum()<30:raise Hold("virtual reference camera does not see enough tabletop")
    rgb=srgb_linear(bgr[:,:,::-1][good]/255.)
    return np.median(rgb,axis=0)

def tune(profile,bench):
    target=float(np.array(profile["table_rgb"])@LUMA)
    bounds=[.125,1024.]
    gain=profile.get("gain",40.)
    best=(float('inf'),gain,None)
    def sample(gain,balance):
        file=DIR/f"lighting-reference-{time.time_ns()}.jpg"
        try:
            presentation(f"sp.set_gain({gain},{list(map(float,balance))!r}); sp.preview({file.name!r})")
            until=time.monotonic()+12
            while (not file.is_file() or file.stat().st_size==0) and time.monotonic()<until:time.sleep(.12)
            return overview_table(file,bench)
        finally:file.unlink(missing_ok=True)
    white=np.array(profile.get("balance",[1.,1.,1.]))
    for _ in range(8):
        measured=sample(gain,white)
        lum=float(measured@LUMA)
        error=math.log2(max(lum,.0002)/max(target,.0002))
        if abs(error)<best[0]:best=abs(error),gain,measured
        if abs(error)<.12:break
        if error>0:bounds[1]=gain
        else:bounds[0]=gain
        gain=math.sqrt(bounds[0]*bounds[1])
    _,gain,measured=best
    desired=np.array(profile["table_rgb"])
    def display_rgb(v):
        return np.where(v<=.0031308,12.92*v,1.055*np.maximum(v,0)**(1/2.4)-.055)
    before=np.max(np.abs(display_rgb(measured)-display_rgb(desired)))
    # Fit a restrained CG light balance against the same matte surface. Preserve the
    # best measured fit if an exposure setting or material limits the achievable match.
    best_color=(before,white.copy(),measured)
    for _ in range(4):
        if best_color[0]<=3/255:break
        white=np.clip(white*np.clip(desired/np.maximum(measured,.002),.55,1.8)**.75,.4,2.5)
        measured=sample(gain,white)
        distance=np.max(np.abs(display_rgb(measured)-display_rgb(desired)))
        if distance<best_color[0]:best_color=(distance,white.copy(),measured)
    distance,white,measured=best_color
    presentation(f"sp.set_gain({gain},{white.tolist()!r})")
    error=abs(math.log2(max(measured@LUMA,.0002)/max(target,.0002)))
    profile.update(gain=gain,balance=white.tolist(),rendered_table_rgb=measured.tolist(),
        table_error_ev=round(error,3),max_table_channel_error_8bit=round(255*distance,2))
    return profile

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--seconds',type=int,default=35);p.add_argument('--serial');p.add_argument('--apply',action='store_true')
    a=p.parse_args()
    if not 3<=a.seconds<=240:p.error('duration must be 3–240 seconds')
    samples=acquire(a.serial,a.seconds)
    profile=fit(samples,json.loads((DIR/'bench.json').read_text()))
    path=DIR/('room-'+str(time.time_ns())+'.hdr')
    write_environment(profile,path);profile['hdr']=path.name
    atomic(DIR/'measurement.json',profile)
    print(f"{profile['exposures']} exposures, {100*profile['coverage']:.0f}% directional coverage, measured key={profile['qualified_key']}")
    if a.apply:
        prior=json.loads((DIR/'active.json').read_text()) if (DIR/'active.json').is_file() else None
        if prior and prior.get('qualified_key') and not profile['qualified_key']:
            raise Hold('Retained the qualified room lighting. Include more room and ceiling directions.')
        for key in ('gain','balance'):
            if prior and key in prior:profile[key]=prior[key]
        try:
            print(presentation('sp.apply_measurement()'))
            profile=tune(profile,json.loads((DIR/'bench.json').read_text()))
            if profile['table_error_ev']>.35:raise Hold('reference exposure did not converge; previous lighting restored')
            atomic(DIR/'active.json',profile)
        except Exception:
            presentation('sp.activate()')
            raise
        print('Table luminance residual:',profile['table_error_ev'],'stops')

if __name__=='__main__':
    try:main()
    except (Hold,OSError,RuntimeError,KeyError,subprocess.SubprocessError) as e:sys.exit(str(e))
