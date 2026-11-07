"""Ten hotel floor compositions. Dimensions and routes are in Unreal centimeters."""
# Widths sum to the same structural footprint; elevator and exterior stay aligned.
SPECS = [
 ('Arrival / classic', [600,600,600,600], [1750,1750,1750,1750], [300,300,300,300]),
 ('Business / staggered', [540,660,540,660], [1690,1810,1690,1750], [320,400,300,320]),
 ('Family / wide suites', [720,510,660,510], [1810,1770,1670,1750], [420,320,340,320]),
 ('Garden / gallery', [510,690,690,510], [1660,1740,1830,1750], [360,440,360,320]),
 ('Executive / lounge pockets', [660,540,510,690], [1840,1760,1680,1750], [460,320,380,340]),
 ('Accessible / broad hall', [570,630,570,630], [1710,1790,1820,1750], [440,480,420,360]),
 ('Corner suites / stepped', [690,510,600,600], [1640,1720,1810,1750], [320,380,440,340]),
 ('Long stay / alternating', [510,720,570,600], [1830,1710,1680,1750], [380,440,320,340]),
 ('Club / gallery lounges', [630,570,690,510], [1680,1800,1840,1750], [420,480,380,340]),
 ('Penthouse / generous foyer', [720,660,510,510], [1800,1680,1720,1750], [480,420,360,340]),
]

ELEVATOR_X=6200
ELEVATOR_APPROACH=6020

def columns(floor):
 name,widths,ys,halls=SPECS[floor-1];widths=[w*1.5 for w in widths];left=-1000;out=[]
 for width,y,hall in zip(widths,ys,halls):
  out.append(dict(x=left+width/2,width=width,y=y,hall=hall,left=left,right=left+width));left+=width
 out += [dict(c,x=c['x']+3600,left=c['left']+3600,right=c['right']+3600) for c in out]
 return out

def hallway(floor,x):
 rows=columns(floor)
 return next((c['y'] for c in rows if c['left']<=x<=c['right']),1750)

def corridor_route(floor,start_x,end_x):
 rows=columns(floor);direction=1 if end_x>=start_x else -1
 points=[]
 for i,c in enumerate(rows):
  points.append((c['x'],c['y']))
  if i+1<len(rows):points.append((c['right'],(c['y']+rows[i+1]['y'])/2))
 points=[p for p in points if min(start_x,end_x)<p[0]<max(start_x,end_x)]
 points.sort(reverse=direction<0)
 points.append((end_x,hallway(floor,end_x)))
 return [(x,y,0 if direction>0 else 180) for x,y in points]


def pedestrian_slots(floor,count):
 """Separate patrol columns and side lanes; never stack people at a column centre."""
 rows=columns(floor);out=[]
 for i in range(count):
  c=rows[min(3,i*4//max(count,1))]
  lane=c['y']+(1 if i%2 else -1)*(c['hall']/2-45)
  out.append((c['x'],lane,(floor-1)*400))
 return out
