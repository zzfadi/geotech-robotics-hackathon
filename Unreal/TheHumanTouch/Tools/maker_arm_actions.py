"""Validate Dream actions in the pinned Maker Arm's URDF units."""
import math
from maker_arm_model import model, mounted_frames


def validate(action,hz=20):
    if not math.isfinite(hz) or hz<=0:raise ValueError('Invalid action rate')
    limits=[j.find('limit') for j in model().findall("joint[@type='revolute']")]
    chunks={}
    for side,key in [('left','q_l_chunk'),('right','q_r_chunk')]:
        rows=action[key]
        if not 1<=len(rows)<=200:raise ValueError('Expected bounded action chunk')
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
            if checked:
                for i,limit in enumerate(limits):
                    speed=min(float(limit.get('velocity')) or 3,3)
                    if abs(row[i]-checked[-1][i])*hz>speed+.001:raise ValueError('Joint velocity exceeds model limit')
            checked.append(list(row))
        chunks[side]=checked
    if len(chunks['left'])!=len(chunks['right']):raise ValueError('Arm chunk lengths differ')
    return chunks
