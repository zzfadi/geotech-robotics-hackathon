"""Hinged guest-room leaves; only the delivery authority animates world transforms."""
import math
import unreal
WIDTH=128.0
HEIGHT=242.0
DURATION=1.8

def pose(origin, opened, south, width=WIDTH):
    # Smoothstep keeps both ends of the swing stationary; the leaf opens inward.
    t=max(0.0,min(1.0,opened));angle=(-1 if south else 1)*100*t*t*(3-2*t)
    r=math.radians(angle)
    return (origin[0]-width/2+width/2*math.cos(r),origin[1]+width/2*math.sin(r),origin[2]+HEIGHT/2+2),angle

class RoomDoors:
    def __init__(self, actors):
        self.doors={};self.amount={}
        for name,a in actors.items():
            if not name.startswith('Hotel_RoomDoor_'):continue
            room=int(name.rsplit('_',1)[1]);suite=actors.get('Hotel_Suite_'+str(room))
            if suite:
                self.doors[room]=(a,tuple(suite.get_actor_location().to_tuple()),(room%100-1)%8<4)
                self.amount[room]=min(1,abs(a.get_actor_rotation().yaw)/100)
    def set_pose(self,room,amount):
        a,origin,south=self.doors[room];p,yaw=pose(origin,amount,south,width=a.get_actor_scale3d().x*100)
        a.set_actor_location_and_rotation(unreal.Vector(*p),unreal.Rotator(yaw=yaw),False,False)
        unreal.SystemLibrary.snapshot_object(a.root_component);self.amount[room]=amount
    def actors(self):return [row[0] for row in self.doors.values()]
