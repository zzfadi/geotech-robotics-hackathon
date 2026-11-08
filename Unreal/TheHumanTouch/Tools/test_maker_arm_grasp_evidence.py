import unittest
from maker_arm_grasp_evidence import update

class GraspEvidence(unittest.TestCase):
    def sample(self, t):
        return dict(monotonic=t,physics_simulated=True,gravity_enabled=True,attachment_checked=True,attached_to=None,
                    height_above_start_cm=11,speed_cm_s=0,lowest_collision_bound_above_table_cm=10,
                    left_pad_surface_distance_cm=.05,right_pad_surface_distance_cm=.05,inside_finite_pad_faces=True,
                    target_tcp_relative_drift_cm=.1,contact_snapshot_complete=True,contact_pair_left=True,
                    contact_pair_right=True,other_contact_bodies=[])
    def test_candidate_never_verified(self):
        s={}
        for i in range(22):update(s,self.sample(i*.1))
        self.assertTrue(s['physical_grasp_candidate']);self.assertFalse(s['grasp_verified'])
    def test_partial_contacts_do_not_prove_no_support(self):
        s={}
        for i in range(22):
            x=self.sample(i*.1);x.pop('contact_snapshot_complete');update(s,x)
        self.assertFalse(s['physical_grasp_candidate'])
        self.assertTrue(s['geometry_review_candidate'])
        self.assertFalse(s['grasp_verified'])
    def test_shelf_and_attachment_rejected(self):
        for change in [dict(other_contact_bodies=['shelf']),dict(attached_to='wrist'),dict(attachment_checked=False)]:
            s={}
            for i in range(22):x=self.sample(i*.1);x.update(change);update(s,x)
            self.assertFalse(s['physical_grasp_candidate'])
            self.assertFalse(s['geometry_review_candidate'])
    def test_sample_gap_resets_hold(self):
        s={};update(s,self.sample(0));update(s,self.sample(3))
        self.assertEqual(s['candidate_hold_seconds'],0)
    def test_out_of_order_rejected(self):
        s={};update(s,self.sample(1))
        with self.assertRaises(ValueError):update(s,self.sample(1))

if __name__=='__main__':unittest.main()
