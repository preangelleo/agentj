"""Offline, generated AAC/MP4 and Opus/WebM through the host's actual pinned demuxer. No owner's audio/ASR API."""
import _hermetic  # noqa: F401
import asyncio
import pathlib
import shutil
import subprocess
import tempfile
import unittest
import wave
from agentj.serve import Host, FF_DEMUX


class Containers(unittest.TestCase):
    def test_recording_containers(self):
        ff = shutil.which('ffmpeg')
        self.assertIsNotNone(ff, 'ffmpeg required for the affected container compatibility check')
        with tempfile.TemporaryDirectory(prefix='p435-audio-') as tmp:
            root = pathlib.Path(tmp)
            for ext, codec, mime in [('m4a', 'aac', 'audio/mp4'), ('webm', 'libopus', 'audio/webm'), ('aac', 'aac', 'audio/aac')]:
                with self.subTest(mime=mime):
                    src, dst = root / ('tone.' + ext), root / ('tone-' + ext + '.wav')
                    subprocess.run([ff, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                                    'sine=frequency=440:duration=1', '-c:a', codec, '-y', str(src)], check=True)
                    host = object.__new__(Host)
                    host.ff_lock = None
                    result = asyncio.run(host._ffmpeg(ff, FF_DEMUX[mime], str(src), str(dst), 10))
                    self.assertEqual(result, 'ok')
                    with wave.open(str(dst)) as wav:
                        self.assertEqual((wav.getnchannels(), wav.getframerate(), wav.getsampwidth()), (1, 16000, 2))
                        self.assertGreaterEqual(wav.getnframes(), 15000)
