"""Validate Dream actions in the pinned Maker Arm's URDF units."""
import math
from maker_arm_model import model, mounted_frames


def validate(action,hz=20,check_speed=True,max_rows=200):
    if not math.isfinite(hz) or hz<=0:raise ValueError('Invalid action rate')
    limits=[j.find('limit') for j in model().findall("joint[@type='revolute']")]
    chunks={}
    for side,key in [('left','q_l_chunk'),('right','q_r_chunk')]:
        rows=action[key]
        if not 1<=len(rows)<=max_rows:raise ValueError('Expected bounded action chunk')
        checked=[]
        for row in rows:
            if len(row)!=7 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in row):raise ValueError('Expected seven finite values')
            if not 0<=row[6]<=1:raise ValueError('Grip outside normalized range')
            row=list(row)
            for i,limit in enumerate(limits):
                low=float(limit.get('lower'));high=float(limit.get('upper'))
                if row[i]<low-1e-4 or row[i]>high+1e-4:raise ValueError('Joint outside CAD limits')
                # Only repair solver roundoff near a bound (under 0.006 degrees).
                row[i]=min(high,max(low,row[i]))
            mounted_frames(row[:6],row[6]*.104825)
            if checked and check_speed:
                for i,limit in enumerate(limits):
                    speed=min(float(limit.get('velocity')) or 3,3)
                    if abs(row[i]-checked[-1][i])*hz>speed+.001:raise ValueError('Joint velocity exceeds model limit')
            checked.append(list(row))
        chunks[side]=checked
    if len(chunks['left'])!=len(chunks['right']):raise ValueError('Arm chunk lengths differ')
    return chunks


def retime(action,state,hz=20,max_duration=15):
    """Preserve target order/positions and uniformly slow playback to CAD speeds.

    Include the actual starting pose so the first grip target cannot jump. No
    out-of-joint-limit or nonfinite target is repaired by this operation.
    """
    # Model aperture predictions can overshoot their normalized endpoints.
    # A finite aperture demand is an actuator command: saturate at the physical
    # closed/open stops. This simulation adapter never permits negative travel,
    # and records every saturation rather than rejecting normal policy overshoot.
    prepared={};corrections=[]
    for key in ('q_l_chunk','q_r_chunk'):
        prepared[key]=[]
        for index,values in enumerate(action[key]):
            row=list(values)
            if len(row)==7 and isinstance(row[6],(int,float)) and math.isfinite(row[6]):
                grip=row[6]
                row[6]=min(1.,max(0.,grip))
                if row[6]!=grip:corrections.append({'arm':key,'row':index,'requested':grip,'applied':row[6]})
            prepared[key].append(row)
    chunks=validate(prepared,hz,check_speed=False)
    root=model();limits=[j.find('limit') for j in root.findall("joint[@type='revolute']")]
    speeds=[min(float(j.get('velocity')) or 3,3) for j in limits]
    jaw_speed=2*float(root.find("joint[@name='gripper_left_joint']/limit").get('velocity'))
    if jaw_speed<=0:raise ValueError('Invalid jaw speed in source model')
    scale=1.
    for side in ('left','right'):
        q=list(state['q_'+side]);gap=float(state['gap_'+side+'_m'])
        mounted_frames(q,gap)
        if max(abs(a-b) for a,b in zip(q,chunks[side][0][:6]))>.02:raise ValueError('Action starts too far from observed pose')
        chunks[side].insert(0,q+[gap/.104825])
        for previous,row in zip(chunks[side],chunks[side][1:]):
            scale=max(scale,*(abs(row[i]-previous[i])*hz/speeds[i] for i in range(6)),abs(row[6]-previous[6])*.104825*hz/jaw_speed)
    rate=hz/scale
    duration=(len(chunks['left'])-1)/rate
    if duration>max_duration:raise ValueError('Speed-limited action exceeds bounded playback duration')
    validate({'q_l_chunk':chunks['left'],'q_r_chunk':chunks['right']},rate,max_rows=201)
    return chunks,{'action_hz':rate,'time_scale':scale,'duration_seconds':duration,'original_rows':len(action['q_l_chunk']),'rows':len(chunks['left']),'grip_limit_corrections':corrections}
