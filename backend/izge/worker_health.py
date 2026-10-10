"""The worker updates this process-local file from an independent pulse thread."""
import os
import time
from pathlib import Path

PATH = Path(os.getenv('WORKER_HEALTH_FILE','/tmp/izge-worker-health'))

def main():
    if not PATH.exists() or time.time()-PATH.stat().st_mtime>45:
        raise SystemExit(1)

if __name__=='__main__':
    main()
