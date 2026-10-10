import os
from pathlib import Path

# Test camera configs live next to the tests; production configs are never modified by tests.
FIXTURES = Path(__file__).parent / 'fixtures' / 'cameras'
os.environ.setdefault('IZGE_CAMERA_CONFIG_DIRS', os.pathsep.join([str(FIXTURES),
                      str(Path(__file__).resolve().parents[2] / 'config' / 'cameras')]))
