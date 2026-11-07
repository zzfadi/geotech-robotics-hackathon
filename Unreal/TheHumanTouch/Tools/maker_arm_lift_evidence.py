"""Measured height/velocity evidence; never declares contact or task success."""
import math

def update(evidence,timers,position,velocity,now):
    if not all(math.isfinite(v) for v in [*position,*velocity,now]):raise ValueError('Nonfinite prop measurement')
    height=position[2]-evidence['initial_z_cm'];speed=math.sqrt(sum(v*v for v in velocity))
    evidence.update(position_cm=list(position),velocity_cm_s=list(velocity),speed_cm_s=speed,
                    height_above_start_cm=height,final_z_cm=position[2],max_z_cm=max(position[2],evidence['max_z_cm']))
    for key,condition in [('above_10cm',height>=10),('slow_above_10cm',height>=10 and speed<=10)]:
        if condition:
            if key not in timers:timers[key]=now
            duration=max(0,now-timers[key])
        else:
            timers.pop(key,None);duration=0.
        evidence['current_'+key+'_seconds']=duration
        maximum='max_'+key+'_seconds';evidence[maximum]=max(duration,evidence.get(maximum,0.))
    evidence['at_least_1s_slow_above_10cm']=evidence['max_slow_above_10cm_seconds']>=1
    # A shelf, collision ejection or unsupported test fixture can satisfy these
    # numeric thresholds. Physical grasp still requires independent review.
    evidence['grasp_verified']=False
