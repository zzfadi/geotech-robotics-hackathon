"""Continuous interpolation of validated, speed-limited policy joint targets."""
import math

def sample(rows,elapsed,hz):
    if not rows or not math.isfinite(elapsed) or not math.isfinite(hz) or hz<=0:
        raise ValueError('Invalid trajectory sampling input')
    cursor=min(max(0.,elapsed*hz),len(rows)-1)
    index=int(cursor);fraction=cursor-index
    following=rows[min(index+1,len(rows)-1)]
    return [a+(b-a)*fraction for a,b in zip(rows[index],following)],index
