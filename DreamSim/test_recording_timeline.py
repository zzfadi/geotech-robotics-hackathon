import json
from pathlib import Path
import tempfile
import unittest
from recording_timeline import write_concat


class TimelineTests(unittest.TestCase):
    def test_hold_and_missing_frame_keep_real_elapsed_time(self):
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)
            items=[]
            for i, stamp in enumerate([10,10.5,13,15],1):
                name='overhead_rec_%06d.jpg'%i
                (folder/name).write_bytes(b'\xff\xd8data\xff\xd9' if i!=2 else b'incomplete')
                items.append({'file':name,'monotonic':stamp})
            (folder/'overhead_timeline.jsonl').write_text('\n'.join(map(json.dumps,items)))
            text=write_concat(folder).read_text()
            self.assertIn('duration 3.000000000',text)
            self.assertIn('duration 2.000000000',text)
            self.assertNotIn('000002.jpg',text)

    def test_rejects_path_and_reversed_time(self):
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root); manifest=folder/'overhead_timeline.jsonl'
            manifest.write_text(json.dumps({'file':'../escape.jpg','monotonic':1}))
            with self.assertRaises(ValueError):write_concat(folder)
            for i in (1,2):(folder/('overhead_rec_%06d.jpg'%i)).write_bytes(b'\xff\xd8\xff\xd9')
            manifest.write_text('\n'.join(json.dumps({'file':'overhead_rec_%06d.jpg'%i,'monotonic':3-i}) for i in (1,2)))
            with self.assertRaises(ValueError):write_concat(folder)
