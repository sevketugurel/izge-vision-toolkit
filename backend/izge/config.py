import os
from pathlib import Path

APP_NAME = os.getenv("IZGE_APP_NAME", "İzge")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://izge:izge@localhost:5432/izge")
MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", "media")).resolve()
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(2 * 1024**3)))
EVIDENCE_RETENTION_DAYS = int(os.getenv("EVIDENCE_RETENTION_DAYS", "90"))
LEASE_SECONDS = 60
HEARTBEAT_SECONDS = 15
MAX_ATTEMPTS = 3
PILOT_CONFIG = {
    "version": "pilot-1", "posture_hz": 1, "interaction_hz": 5, "max_gap": 5,
    "posture_transition": 3, "sequence_seconds": 30, "repeat_window": 120,
    "repeat_count": 3, "interference_seconds": 15, "confidence": .70,
    "notification_cooldown": 30, "close_silence": 120, "evidence_padding": 10,
}
