"""Per-cup measured evidence for free-form tasks; never declares task success."""
import math
import time

CUPS = {'MakerStation_Prop_Cup'+suffix for suffix in ('','_02','_03','_04','_05')}

def finite(x):
    return type(x) in (int,float) and math.isfinite(x)


def cup_metrics(status, now=None):
    now=time.time() if now is None else now
    stamp=(status or {}).get('sampled_unix')
    fresh=finite(stamp) and finite(now) and 0<=now-stamp<=2
    result={}
    for name,p in (status or {}).get('props',{}).items():
        if name not in CUPS:continue
        height=p.get('height_above_start_cm');hold=p.get('current_slow_above_10cm_seconds');speed=p.get('speed_cm_s')
        physical=(p.get('physics_simulated') is True and p.get('gravity_enabled') is True
                  and 'attached_to' in p and p['attached_to'] is None)
        valid=fresh and all(finite(x) for x in (height,hold,speed)) and hold>=0 and speed>=0
        candidate=valid and physical and height>=10 and speed<=10
        result[name]={'target':name,'fresh':fresh,'height_cm':height,'held_slow_above_10cm_s':hold,
                      'max_height_cm':p.get('max_z_cm',0)-p.get('initial_z_cm',0),
                      'tilt_degrees':p.get('tilt_degrees'),'speed_cm_s':speed,
                      'physics_simulated':p.get('physics_simulated'),'gravity_enabled':p.get('gravity_enabled'),
                      'attached_to':p.get('attached_to'),'measured':valid,
                      'measurement_scope':'cup center rise; not lowest-point clearance or grasp proof',
                      'lift_candidate':candidate,'lift_held':candidate and hold>=5,
                      'required_hold_seconds':5,'grasp_verified':False,'task_completed':False}
    return result


def candidate(metrics):
    """One actual object at a time; never sum hold duration across cups."""
    eligible=[name for name,m in metrics.items() if m['lift_candidate']]
    return max(eligible,key=lambda name:(metrics[name]['held_slow_above_10cm_s'],name),default=None)
