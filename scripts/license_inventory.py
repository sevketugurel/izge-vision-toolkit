"""Record the actual installed distributions, codec builds and platform."""
import importlib.metadata as metadata
import json
import platform
import subprocess
from pathlib import Path

def capture(args):
    try:
        return subprocess.run(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,text=True,timeout=20,check=True).stdout
    except (OSError,subprocess.SubprocessError):
        return 'unavailable'

packages=[]
for dist in metadata.distributions():
    m=dist.metadata
    packages.append({'name':m['Name'],'version':dist.version,'license':m.get('License-Expression') or m.get('License'),
                     'license_classifiers':[x for x in m.get_all('Classifier',[]) if x.startswith('License ::')],
                     'license_files':[str(f) for f in (dist.files or []) if 'license' in str(f).lower() or 'copying' in str(f).lower()]})
try:
    import av
    codecs={'pyav':av.__version__,'library_versions':av.library_versions}
except ImportError:
    codecs={'pyav':'not installed'}
lock=Path('frontend/package-lock.json')
node=json.loads(lock.read_text())['packages'] if lock.exists() else {}
print(json.dumps({'platform':platform.platform(),'python':platform.python_version(),
    'python_distributions':sorted(packages,key=lambda x:x['name'].lower()),'node_packages':node,
    'ffmpeg_version':capture(['ffmpeg','-version']),'ffmpeg_license':capture(['ffmpeg','-L']),
    'pyav_libraries':codecs,'system_packages':capture(['dpkg-query','-W'])},ensure_ascii=False,indent=2))
