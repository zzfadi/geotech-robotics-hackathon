"""Frame-rate-independent camera rig for sparse replicated robot poses (centimeters)."""
import math


def angle_delta(target, current):
    return (target-current+180.0) % 360.0-180.0


class SmoothRig:
    def __init__(self):
        self.position = None
        self.orbit = None
        self.distance = 175.0
        self.height = 160.0
        self.docked = False

    def step(self, position, actor_yaw, dt):
        dt = min(max(float(dt), 0.0), 0.1)
        x, y, z = position
        # Hysteresis prevents replicated poses near a bay boundary flipping the shot.
        if self.docked:
            self.docked = x < -2650 and y > 2000
        else:
            self.docked = x < -2750 and y > 2100
        desired_orbit = math.degrees(math.atan2(-240, 100)) if self.docked else actor_yaw+270
        distance = 260.0 if self.docked else 175.0
        height = 170.0 if self.docked else 160.0
        if self.position is None or math.dist(position, self.position) > 500:
            self.position = tuple(position)
            self.orbit, self.distance, self.height = desired_orbit, distance, height
        else:
            alpha = -math.expm1(-6.0*dt)
            self.position = tuple(a+(b-a)*alpha for a,b in zip(self.position,position))
            turn = angle_delta(desired_orbit,self.orbit)*(-math.expm1(-2.5*dt))
            self.orbit += max(-60*dt,min(60*dt,turn))
            self.distance += (distance-self.distance)*alpha
            self.height += (height-self.height)*alpha
        p = self.position
        yaw = math.radians(self.orbit)
        camera = (p[0]+self.distance*math.cos(yaw),p[1]+self.distance*math.sin(yaw),p[2]+self.height)
        # Both camera and aim use the same filtered pose. A raw aim target jitters
        # whenever Multi-User delivers a transform, even with smoothed camera position.
        return camera, (p[0],p[1],p[2]+65)
