"""Verify the headset survey's mechanical landmarks against the pinned CAD solid."""
import collections
import math
import struct
import unittest

from maker_arm_model import MODEL, model


class MakerArmSurveyDatums(unittest.TestCase):
    def test_flange_holes_and_tool_witness(self):
        robot = model()
        base = robot.find("link[@name='base_link']")
        flange = next(v for v in base.findall("visual")
                      if "part_012_" in v.find("geometry/mesh").get("filename"))
        mesh = flange.find("geometry/mesh")
        self.assertEqual(list(map(float, mesh.get("scale").split())), [0.001] * 3)
        self.assertAlmostEqual(float(flange.find("origin").get("xyz").split()[1]), -0.0257, places=7)
        data = (MODEL.parent / mesh.get("filename")).read_bytes()
        # Unpaired edges of the top-face triangles form the plate outline and
        # the actual bore rims. Their bounding boxes distinguish outer holes.
        edges = collections.Counter()
        for tri in struct.iter_unpack("<12fH", data[84:]):
            pts = [tuple(round(v, 4) for v in tri[i:i + 3]) for i in (3, 6, 9)]
            if all(abs(p[1]) < 0.0001 for p in pts):
                for a, b in zip(pts, pts[1:] + pts[:1]):
                    edges[tuple(sorted((a, b)))] += 1
        neighbors = collections.defaultdict(set)
        for (a, b), count in edges.items():
            if count == 1:
                neighbors[a].add(b)
                neighbors[b].add(a)
        rims = set()
        while neighbors:
            pending = [next(iter(neighbors))]
            group = set()
            while pending:
                point = pending.pop()
                if point not in group:
                    group.add(point)
                    pending.extend(neighbors.pop(point, ()))
            x, _, z = zip(*group)
            if math.isclose(max(x) - min(x), 3.5, abs_tol=0.01) and math.isclose(max(z) - min(z), 3.5, abs_tol=0.01):
                rims.add(((min(x) + max(x)) / 2, (min(z) + max(z)) / 2))
        self.assertEqual(rims, {(x, z) for x in (-40, 0, 40) for z in (-90, 90)})
        tcp = robot.find("joint[@name='grasp_center_joint']")
        self.assertEqual(tcp.find("parent").get("link"), "link_007")
        self.assertLess(math.dist(list(map(float, tcp.find("origin").get("xyz").split())), [0, 0, -0.1696387]), 1e-7)


if __name__ == "__main__":
    unittest.main()
