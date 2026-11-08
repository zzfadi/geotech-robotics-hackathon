import math, unittest
from maker_arm_contact_stop import local_bounds_points, cup_contact_shape, contact_gap, pad_points, broad_phase

class CupContact(unittest.TestCase):
    def shape(self, tilt=0, yaw=0, offset=(0,0,-.17)):
        shape=cup_contact_shape((-.04,-.04,-.06),(.04,.04,.06))
        c,s=math.cos(tilt),math.sin(tilt)
        cy,sy=math.cos(yaw),math.sin(yaw)
        points=[]
        for x,y,z in shape['points']:
            x,y=cy*x-sy*y,sy*x+cy*y
            points.append([x+offset[0],c*y-s*z+offset[1],s*y+c*z+offset[2]])
        return dict(shape,points=points)

    def test_midheight_taper_not_rim_aabb(self):
        gap,names=contact_gap(0,.10,[('cup',self.shape())],flat=True)
        self.assertAlmostEqual(gap,.07166666666666667-.005,delta=.000025)
        self.assertEqual(names,['cup'])
        self.assertLess(gap,.075)

    def test_yaw_invariance_without_underestimating_round_support(self):
        expected=.07166666666666667-.005
        for degrees in range(0,180,7):
            gap,_=contact_gap(0,.10,[('cup',self.shape(yaw=math.radians(degrees)))],flat=True)
            self.assertGreaterEqual(gap,expected-1e-10)
            self.assertLess(gap-expected,.000025)

    def test_tilted_frustum_clips_finite_pad_corridor(self):
        gap,names=contact_gap(0,.10,[('cup',self.shape(tilt=math.radians(10)))],flat=True)
        self.assertGreater(gap,.06)
        self.assertLess(gap,.08)
        self.assertEqual(names,['cup'])

    def test_tilted_support_encloses_fine_round_surface_samples(self):
        # Independent dense true-frustum samples inside the pad corridor must
        # never project wider than the conservative clipped actuator envelope.
        tilt=.6;c,s=math.cos(tilt),math.sin(tilt)
        clipped=pad_points(self.shape(tilt=tilt,yaw=.37),flat=True)
        lo=min(p[1] for p in clipped);hi=max(p[1] for p in clipped)
        sampled=[]
        for iz in range(121):
            z=-.06+iz*.001;r=.03+(z+.06)/.12*.01
            for ia in range(720):
                a=ia*2*math.pi/720;x=r*math.cos(a);y=r*math.sin(a)
                p=(x,c*y-s*z,s*y+c*z-.17)
                if -.015<=p[0]<=.015 and -.18<=p[2]<=-.16:sampled.append(p[1])
        self.assertTrue(sampled)
        self.assertLessEqual(lo,min(sampled))
        self.assertGreaterEqual(hi,max(sampled))

    def test_finite_corridor_and_one_sided_cup_do_not_latch(self):
        for offset in ((.10,0,-.17),(0,.10,-.17),(0,0,.1)):
            self.assertEqual(contact_gap(.01,.1,[('cup',self.shape(offset=offset))],flat=True),(.01,[]))

    def test_compression_and_no_forced_open_preserved(self):
        shape=self.shape()
        gap,_=contact_gap(0,.1,[('cup',shape)],compliance=.005,flat=True)
        width=max(p[1] for p in pad_points(shape,True))-min(p[1] for p in pad_points(shape,True))
        self.assertAlmostEqual(gap,width-.005)
        self.assertEqual(contact_gap(0,.02,[('cup',shape)],flat=True)[0],.02)
        self.assertEqual(contact_gap(.10,.02,[('cup',shape)],flat=True),(.10,[]))

    def test_broad_phase_only_rejects_outside_candidates(self):
        corners=local_bounds_points((-.04,-.04,-.23),(.04,.04,-.11))
        self.assertTrue(broad_phase(corners))
        self.assertFalse(broad_phase([[x+.2,y,z] for x,y,z in corners]))
        self.assertFalse(broad_phase([[x,y,z+.4] for x,y,z in corners]))

    def test_legacy_box_unchanged(self):
        points=local_bounds_points((-.04,-.04,-.06),(.04,.04,.06))
        self.assertTrue(all(abs(p[0])==.04 and abs(p[1])==.04 for p in points))
