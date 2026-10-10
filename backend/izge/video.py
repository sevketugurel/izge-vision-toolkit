"""CPU media adapters. Originals never share a path with user-supplied filenames."""
import json
import os
import re
import subprocess
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from fractions import Fraction
from .domain import Frame, Observation
from .storage import LocalStorage  # noqa: F401  (re-exported for existing imports)

def run_tool(args: list[str], timeout=120) -> bytes:
    try:
        result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=timeout, check=True)
        return result.stdout
    except (subprocess.SubprocessError, OSError) as exc:
        raise ValueError("Video aracı işlemi tamamlayamadı") from exc


def probe(path: Path) -> dict:
    data = json.loads(run_tool(['ffprobe','-v','error','-protocol_whitelist','file,pipe',
        '-probesize','10000000','-analyzeduration','10000000','-select_streams','v:0','-show_entries',
        'stream=codec_type,codec_name,width,height,time_base,start_time:format=format_name,duration','-of','json',str(path)],30))
    streams = [s for s in data.get('streams',[]) if s['codec_type']=='video']
    if not streams:
        raise ValueError("Video akışı bulunamadı")
    s, fmt = streams[0], data['format']
    if not set(fmt['format_name'].split(',')) & {'mov','mp4','matroska','avi'}:
        raise ValueError("Desteklenmeyen video kapsayıcısı")
    duration = float(fmt.get('duration',0))
    width, height = int(s['width']), int(s['height'])
    if not 0 < duration <= 86400 or not 0 < width <= 8192 or not 0 < height <= 8192:
        raise ValueError("Video süre veya çözünürlük sınırı dışında")
    return dict(duration=duration,width=width,height=height,codec=s['codec_name'],
                time_base=s['time_base'],start_time=float(s.get('start_time',0)),format=fmt['format_name'])


def derivative(source: Path, dest: Path, kind: str, start=0., end: float | None=None):
    args = ['ffmpeg','-nostdin','-v','error','-y','-max_alloc','268435456','-threads','2','-protocol_whitelist','file,pipe','-i',str(source),'-ss',str(start)]
    if end is not None:
        args += ['-t',str(max(.001,end-start))]
    args += ['-map','0:v:0','-an','-sn','-dn']
    if kind == 'thumbnail':
        args += ['-frames:v','1','-vf','scale=640:-2']
    else:
        args += ['-vf',"scale=w='min(1280,iw)':h=-2",'-c:v','libx264','-preset','fast','-crf','25','-threads','2','-pix_fmt','yuv420p','-movflags','+faststart']
    run_tool(args+[str(dest)], timeout=600)


class FileVideoSource:
    def __init__(self, path: Path):
        self.path = path
    def frames(self, start=0):
        import av
        with av.open(str(self.path), options={'protocol_whitelist':'file,pipe'}) as container:
            stream = container.streams.video[0]
            stream.thread_count = 2
            origin = stream.start_time or 0
            if start > 0:
                container.seek(max(origin, int(start/stream.time_base)+origin), stream=stream, backward=True)
            for frame in container.decode(stream):
                if frame.pts is None:
                    continue
                offset = float((frame.pts-origin)*frame.time_base)
                if offset < start:
                    continue
                yield Frame(frame.pts, Fraction(frame.time_base), offset, frame.width, frame.height, frame)


class UnavailableAnalyzer:
    version = 'unavailable-1'
    def observe(self, frame: Frame):
        return [Observation(offset=frame.offset, track_id='unresolved', pts=frame.pts,
                            time_base=str(frame.time_base))]


class DemoAnalyzer:
    version = 'demo-1'
    def __init__(self, identities):
        self.identities = identities
    def observe(self, frame: Frame):
        rows = []
        identities = self.identities(frame.offset) if callable(self.identities) else self.identities
        if not identities:
            return [Observation(frame.offset, "unresolved", origin="demo", producer=self.version, pts=frame.pts, time_base=str(frame.time_base))]
        for i,(animal,stall) in enumerate(identities):
            phase = int(frame.offset) % 9
            signal = {1:'approach',3:'interference',5:'withdrawal'}.get(phase) if i == 0 and frame.offset-int(frame.offset) < .1 else None
            rows.append(Observation(offset=frame.offset, track_id=f'demo-track-{i}', animal_id=animal,
                stall_id=stall, identity='resolved', body_visible=True, head_visible=True, feeder_visible=True,
                posture='standing' if i != 1 else 'lying', activity='feeding' if i != 1 else 'idle',
                quality=.9, origin='demo', producer=self.version, signal=signal,
                actor_id=identities[1][0] if signal == 'interference' and len(identities)>1 else None,
                actor_identity='resolved' if signal == 'interference' and len(identities)>1 else 'unknown',
                feed_present=True, confidence=.95, pts=frame.pts, time_base=str(frame.time_base)))
        return rows


class RTSPVideoSource:
    """Optional live input adapter; callers must persist gap/health records. Not an NVR service."""
    def __init__(self, secret_ref: str, stale_seconds=5):
        if not re.fullmatch(r'IZGE_RTSP_[A-Z0-9_]+', secret_ref):
            raise ValueError('Geçersiz secret referansı')
        self.secret_ref, self.stale_seconds = secret_ref, stale_seconds
        self.buffer = deque(maxlen=2)
        self.stop = threading.Event()
        self.health = {'state':'disconnected','last_frame':None,'dropped':0,'error':None}
        self.gaps = deque(maxlen=1000)
        self.lock = threading.Lock()
    def _read(self):
        import av
        av.logging.set_level(None)  # Never forward credential-bearing codec messages.
        delay = 1
        while not self.stop.is_set():
            try:
                url = os.environ[self.secret_ref]
                if not url.startswith(('rtsp://','rtsps://')):
                    raise ValueError('Invalid protocol')
                with av.open(url, timeout=(5.,5.), options={'rtsp_transport':'tcp'}) as container:
                    self.health.update(state='connected', error=None)
                    stream = container.streams.video[0]
                    stream.thread_count = 2
                    origin, last_pts, connected_at = None, None, time.monotonic()
                    for f in container.decode(stream):
                        if self.stop.is_set():
                            return
                        if f.pts is None:
                            continue
                        origin = f.pts if origin is None else origin
                        now = datetime.now(timezone.utc)
                        offset = float((f.pts-origin)*f.time_base)
                        if (last_pts is not None and f.pts <= last_pts) or time.monotonic()-connected_at-offset > self.stale_seconds:
                            self.health.update(state='stale', error='Eski veya gecikmiş kare')
                            self.gaps.append({'reason':'stale_source_frame','at':now.isoformat()})
                            continue
                        last_pts = f.pts
                        self.health.update(state='connected', error=None)
                        frame = Frame(f.pts,Fraction(f.time_base),float((f.pts-origin)*f.time_base),f.width,f.height,f,received_at=now)
                        with self.lock:
                            if len(self.buffer)==2:
                                self.health['dropped'] += 1
                                self.gaps.append({'reason':'buffer_overflow','at':now.isoformat()})
                            self.buffer.append((time.monotonic(),frame))
                        self.health['last_frame'] = now.isoformat()
                        delay = 1
            except Exception:
                # Deliberately never expose exception strings: codecs may embed credential URLs.
                self.health.update(state='disconnected', error='Bağlantı veya okuma başarısız')
                self.gaps.append({'reason':'disconnected','at':datetime.now(timezone.utc).isoformat()})
            self.stop.wait(delay)
            delay = min(30,delay*2)
    def frames(self, start=0):
        thread = threading.Thread(target=self._read, daemon=True)
        thread.start()
        try:
            while not self.stop.is_set():
                with self.lock:
                    item = self.buffer.popleft() if self.buffer else None
                if item:
                    arrived,frame = item
                    if time.monotonic()-arrived <= self.stale_seconds:
                        yield frame
                    else:
                        self.gaps.append({'reason':'stale_frame','at':datetime.now(timezone.utc).isoformat()})
                else:
                    self.stop.wait(.02)
        finally:
            self.stop.set()
            thread.join(timeout=6)
