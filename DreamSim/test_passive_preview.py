import json
import tempfile
import unittest
from pathlib import Path
from maker_arm_passive_preview import passive_indices, due_camera, PassivePreview
from fleet_worker import passive_preview_frames, table_progress


class PassiveCameras(unittest.TestCase):
    def test_unselected_only_and_round_robin_budget(self):
        self.assertEqual(passive_indices([0]),list(range(1,9)))
        self.assertEqual(passive_indices(list(range(9))),[])
        with self.assertRaises(ValueError):passive_indices([9])
        with tempfile.TemporaryDirectory() as directory:
            preview=PassivePreview([0],directory)
            preview.items={i:{'next_capture':0.,'pending':None,'sequence':0} for i in range(1,9)}
            captured=[]
            def export(item,path):
                captured.append(path)
                path.write_bytes(b'\xff\xd8camera\xff\xd9')
            preview.tick(10,0,export)
            self.assertEqual(captured,[])
            preview.tick(10,1,export)
            self.assertEqual(len(captured),1)
            self.assertEqual(preview.items[1]['next_capture'],11.)
            self.assertEqual(due_camera(preview.items,10),2)
            preview.tick(10.1,1,export)
            self.assertEqual(len(captured),2)
            packet=json.loads((Path(directory)/'passive-preview/frames.json').read_text())
            self.assertEqual(set(packet['frames']),{'T01'})
            self.assertEqual(packet['scope'],'passive-sie-camera-only')
            frames,stamps=passive_preview_frames(directory,now=packet['frame_times']['T01'])
            self.assertEqual(set(frames),{'T01'})
            self.assertEqual(passive_preview_frames(directory,now=packet['frame_times']['T01']+6),({},{}))
            self.assertEqual(table_progress({'tables':{}},[]),{})
