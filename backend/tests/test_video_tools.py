import unittest
import tempfile
import uuid
from pathlib import Path
from izge.video import LocalStorage, run_tool, probe, derivative, FileVideoSource, RTSPVideoSource

class VideoToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.root=Path(cls.temp.name)
        cls.source=cls.root/'source.mp4'
        run_tool(['ffmpeg','-nostdin','-v','error','-y','-f','lavfi','-i','testsrc2=size=160x120:rate=10:duration=2',
                  '-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(cls.source)])
    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()
    def test_actual_probe_thumbnail_preview_clip(self):
        metadata=probe(self.source)
        self.assertEqual(metadata['width'],160)
        self.assertAlmostEqual(metadata['duration'],2,places=1)
        thumbnail=self.root/'thumbnail.jpg'
        derivative(self.source,thumbnail,'thumbnail')
        self.assertTrue(thumbnail.read_bytes().startswith(b'\xff\xd8'))
        preview=self.root/'preview.mp4'
        derivative(self.source,preview,'preview')
        self.assertEqual(probe(preview)['codec'],'h264')
        clip=self.root/'clip.mp4'
        derivative(self.source,clip,'clip',.5,1.5)
        self.assertAlmostEqual(probe(clip)['duration'],1,places=1)
    def test_invalid_content_rejected(self):
        invalid=self.root/'invalid.mp4'
        invalid.write_text('not a video')
        with self.assertRaises(ValueError):
            probe(invalid)
    def test_storage_path_and_symlink_rejected(self):
        storage=LocalStorage(self.root)
        for key in ('../../etc/passwd','/tmp/file','file.mp4','a'*32+'.mp4/../x'):
            with self.assertRaises(ValueError):
                storage.path(key)
        key=uuid.uuid4().hex+'.mp4'
        self.assertEqual(storage.path(key).parent,self.root)
        storage.path(key).symlink_to(self.source)
        with self.assertRaises(ValueError):
            storage.path(key)
    def test_secret_url_not_accepted_as_reference(self):
        with self.assertRaises(ValueError):
            RTSPVideoSource('rtsp://alice:password@host')
        live=RTSPVideoSource('IZGE_RTSP_TEST')
        self.assertEqual(live.buffer.maxlen,2)
        self.assertNotIn('secret',str(live.health))
    def test_real_pts_decode(self):
        try:
            __import__("av")
        except ImportError:
            self.skipTest('PyAV not installed; codec decode cannot be verified in this environment')
        rows=list(FileVideoSource(self.source).frames())
        self.assertEqual(len(rows),20)
        self.assertAlmostEqual(rows[-1].offset,1.9)
        self.assertTrue(all(b.pts>a.pts for a,b in zip(rows,rows[1:])))

if __name__=='__main__':
    unittest.main()
