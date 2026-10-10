"""Persistence schema. Composite references preserve the farm boundary in the database."""
import uuid
from sqlalchemy import (MetaData, Table, Column as C, String, Text, Integer, Float, Boolean,
                        Date, DateTime, ForeignKey, ForeignKeyConstraint, Index, UniqueConstraint, CheckConstraint, func, literal_column)
from sqlalchemy.dialects.postgresql import UUID, JSONB, ExcludeConstraint

metadata = MetaData()

def ident():
    return C("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

def stamp():
    return C("created_at", DateTime(timezone=True), nullable=False, server_default=func.now())

def scoped(name, *cols, constraints=()):
    return Table(name, metadata, ident(), C("farm_id", UUID(as_uuid=True), ForeignKey("farms.id"), nullable=False),
                 stamp(), *cols, UniqueConstraint("farm_id", "id"), *constraints)

def ref(col, target):
    return ForeignKeyConstraint(["farm_id", col], [target + ".farm_id", target + ".id"])

farms = Table("farms", metadata, ident(), stamp(), C("name", String(120), nullable=False),
              C("timezone", String(80), nullable=False, default="Europe/Istanbul"),
              C("is_demo", Boolean, nullable=False, default=False))
barns = scoped("barns", C("name", String(120), nullable=False))
cameras = scoped("cameras", C("name", String(120), nullable=False), C("barn_id", UUID, nullable=False),
    C("geometry_version", Integer, nullable=False, default=1), C("width", Integer), C("height", Integer),
    C("geometry_valid", Boolean, nullable=False, default=False), C("clock_verified", Boolean, nullable=False, default=False),
    C("secret_ref", String(120)), C("health", JSONB, nullable=False, default=dict), C("config_key", String(80)),
    constraints=(ref("barn_id", "barns"),))
stalls = scoped("stalls", C("name", String(120), nullable=False), C("barn_id", UUID, nullable=False),
               constraints=(ref("barn_id", "barns"), UniqueConstraint("farm_id", "barn_id", "id")))
animals = scoped("animals", C("number", String(80), nullable=False), C("name", String(120), nullable=False, default=""),
                 constraints=(UniqueConstraint("farm_id", "number"),))
occupancies = scoped("occupancies", C("animal_id", UUID, nullable=False), C("stall_id", UUID, nullable=False),
    C("start", DateTime(timezone=True), nullable=False), C("end", DateTime(timezone=True)),
    constraints=(ref("animal_id", "animals"), ref("stall_id", "stalls"), CheckConstraint('"end" IS NULL OR "end" > start'),
        ExcludeConstraint(("farm_id", "="), ("animal_id", "="), (literal_column("tstzrange(start, \"end\", '[)')"), "&&"), name="animal_no_overlap", using="gist"),
        ExcludeConstraint(("farm_id", "="), ("stall_id", "="), (literal_column("tstzrange(start, \"end\", '[)')"), "&&"), name="stall_no_overlap", using="gist")))
neighbors = scoped("neighbors", C("barn_id", UUID, nullable=False), C("left_id", UUID, nullable=False), C("right_id", UUID, nullable=False),
    constraints=(CheckConstraint("left_id < right_id"), UniqueConstraint("farm_id", "left_id", "right_id"),
        ForeignKeyConstraint(["farm_id", "barn_id", "left_id"], ["stalls.farm_id", "stalls.barn_id", "stalls.id"]),
        ForeignKeyConstraint(["farm_id", "barn_id", "right_id"], ["stalls.farm_id", "stalls.barn_id", "stalls.id"])))
zones = scoped("zones", C("camera_id", UUID, nullable=False), C("stall_id", UUID, nullable=False),
    C("role", String(20), nullable=False), C("polygon", JSONB, nullable=False), C("width", Integer, nullable=False),
    C("height", Integer, nullable=False), C("geometry_version", Integer, nullable=False),
    constraints=(ref("camera_id", "cameras"), ref("stall_id", "stalls"), UniqueConstraint("farm_id", "camera_id", "geometry_version", "stall_id", "role"), CheckConstraint("role IN ('body','head','feeder')")))
media = scoped("media", C("camera_id", UUID, nullable=False), C("storage_key", String(100), nullable=False, unique=True),
    C("checksum", String(64), nullable=False), C("filename", String(200), nullable=False), C("metadata", JSONB, nullable=False),
    C("origin", String(20), nullable=False, default="unavailable"), C("current_time_version", Integer, nullable=False, default=0),
    constraints=(ref("camera_id", "cameras"),))
time_maps = scoped("time_maps", C("media_id", UUID, nullable=False), C("version", Integer, nullable=False),
    C("recorded_at", DateTime(timezone=True), nullable=False), C("note", Text, nullable=False),
    constraints=(ref("media_id", "media"), UniqueConstraint("farm_id", "media_id", "version")))
runs = scoped("runs", C("media_id", UUID, nullable=False), C("fingerprint", String(64), nullable=False, unique=True),
    C("analyzer", String(80), nullable=False), C("origin", String(20), nullable=False), C("context", JSONB, nullable=False),
    C("start_offset", Float, nullable=False), C("end_offset", Float, nullable=False),
    C("status", String(20), nullable=False, default="queued"), C("published_at", DateTime(timezone=True)),
    constraints=(ref("media_id", "media"), CheckConstraint("start_offset >= 0 AND end_offset > start_offset")))
jobs = scoped("jobs", C("run_id", UUID, nullable=False, unique=True), C("status", String(20), nullable=False, default="queued"),
    C("attempts", Integer, nullable=False, default=0), C("lease_token", UUID), C("lease_until", DateTime(timezone=True)),
    C("available_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    C("checkpoint", Float, nullable=False, default=-1), C("progress", Float, nullable=False, default=0), C("error", Text),
    constraints=(ref("run_id", "runs"),))
observations = scoped("observations", C("run_id", UUID, nullable=False), C("sample_key", String(120), nullable=False),
    C("offset", Float, nullable=False), C("animal_id", UUID), C("stall_id", UUID), C("payload", JSONB, nullable=False),
    constraints=(ref("run_id", "runs"), ref("animal_id", "animals"), ref("stall_id", "stalls"), UniqueConstraint("run_id", "sample_key")))
intervals = scoped("intervals", C("run_id", UUID, nullable=False), C("animal_id", UUID, nullable=False),
    C("start_offset", Float, nullable=False), C("end_offset", Float, nullable=False), C("dimension", String(20), nullable=False),
    C("label", String(40), nullable=False), C("quality", Float, nullable=False), C("origin", String(20), nullable=False),
    constraints=(ref("run_id", "runs"), ref("animal_id", "animals"), CheckConstraint("end_offset > start_offset")))
events = scoped("events", C("run_id", UUID, nullable=False), C("event_key", String(150), nullable=False),
    C("start_offset", Float, nullable=False), C("end_offset", Float, nullable=False), C("kind", String(80), nullable=False),
    C("origin", String(20), nullable=False), C("confidence", Float), C("sufficient", Boolean, nullable=False),
    C("details", JSONB, nullable=False), constraints=(ref("run_id", "runs"), UniqueConstraint("run_id", "event_key")))
participants = scoped("participants", C("event_id", UUID, nullable=False), C("animal_id", UUID, nullable=False),
    C("role", String(30), nullable=False), constraints=(ref("event_id", "events"), ref("animal_id", "animals"), UniqueConstraint("event_id", "animal_id", "role")))
evidence = scoped("evidence", C("media_id", UUID, nullable=False), C("event_id", UUID), C("kind", String(20), nullable=False),
    C("storage_key", String(100), nullable=False), C("start_offset", Float, nullable=False, default=0), C("end_offset", Float),
    C("retained", Boolean, nullable=False, default=False), C("expires_at", DateTime(timezone=True)),
    constraints=(ref("media_id", "media"), ref("event_id", "events")))
feeding_windows = scoped("feeding_windows", C("barn_id", UUID, nullable=False), C("start", DateTime(timezone=True), nullable=False),
    C("end", DateTime(timezone=True), nullable=False), constraints=(ref("barn_id", "barns"), CheckConstraint('"end" > start')))
feed_presence = scoped("feed_presence", C("stall_id", UUID, nullable=False), C("start", DateTime(timezone=True), nullable=False),
    C("end", DateTime(timezone=True), nullable=False), C("present", Boolean), C("origin", String(20), nullable=False),
    constraints=(ref("stall_id", "stalls"), CheckConstraint('"end" > start')))
annotations = scoped("annotations", C("media_id", UUID, nullable=False), C("animal_id", UUID), C("start_offset", Float, nullable=False),
    C("end_offset", Float, nullable=False), C("label", String(100), nullable=False), C("note", Text, nullable=False, default=""),
    C("supersedes_id", UUID), C("time_version", Integer, nullable=False), C("origin", String(20), nullable=False, default="manual"),
    constraints=(ref("media_id", "media"), ref("animal_id", "animals"), ref("supersedes_id", "annotations"), CheckConstraint("start_offset >= 0 AND end_offset > start_offset")))
reviews = scoped("reviews", C("event_id", UUID, nullable=False), C("verdict", String(30), nullable=False),
    C("note", Text, nullable=False), C("origin", String(20), nullable=False, default="manual"),
    constraints=(ref("event_id", "events"), CheckConstraint("verdict IN ('confirmed','rejected','needs_evidence')")))
notifications = scoped("notifications", C("event_id", UUID, nullable=False, unique=True), C("message", Text, nullable=False),
    constraints=(ref("event_id", "events"),))
system_events = scoped("system_events", C("kind", String(80), nullable=False), C("details", JSONB, nullable=False))

# Migration 0002: stall-level feeding pipeline. Identity is the stall position from camera config.
STALL_STATES = "'yemde','yemde_degil','gorunmuyor'"
crop_samples = scoped("crop_samples", C("media_id", UUID, nullable=False), C("camera_id", UUID, nullable=False),
    C("run_id", UUID, nullable=False), C("stall_key", String(40), nullable=False), C("offset", Float, nullable=False),
    C("captured_at", DateTime(timezone=True), nullable=False), C("local_hour", Integer, nullable=False),
    C("daynight", String(6), nullable=False), C("crop_key", String(160), nullable=False),
    C("context_key", String(160), nullable=False),
    constraints=(ref("media_id", "media"), ref("camera_id", "cameras"), ref("run_id", "runs"),
        UniqueConstraint("run_id", "stall_key", "offset"), CheckConstraint("daynight IN ('gunduz','gece')")))
crop_labels = scoped("crop_labels", C("sample_id", UUID, nullable=False), C("label", String(20), nullable=False),
    C("labeler", String(80), nullable=False), C("supersedes_id", UUID), C("origin", String(20), nullable=False, default="manual"),
    constraints=(ref("sample_id", "crop_samples"), ref("supersedes_id", "crop_labels"),
        CheckConstraint(f"label IN ({STALL_STATES},'skipped','retracted')")))
feeding_models = scoped("feeding_models", C("version", String(80), nullable=False), C("backbone", String(120), nullable=False),
    C("classifier", String(40), nullable=False), C("label_count", Integer, nullable=False),
    C("class_counts", JSONB, nullable=False), C("metrics", JSONB, nullable=False), C("train_days", JSONB, nullable=False),
    C("excluded_days", JSONB, nullable=False), C("storage_key", String(160), nullable=False),
    constraints=(UniqueConstraint("farm_id", "version"),))
feeding_segments = scoped("feeding_segments", C("run_id", UUID, nullable=False), C("camera_id", UUID, nullable=False),
    C("stall_key", String(40), nullable=False), C("state", String(20), nullable=False),
    C("start_offset", Float, nullable=False), C("end_offset", Float, nullable=False),
    C("start_at", DateTime(timezone=True), nullable=False), C("end_at", DateTime(timezone=True), nullable=False),
    C("confidence", Float), C("origin", String(20), nullable=False),
    constraints=(ref("run_id", "runs"), ref("camera_id", "cameras"), CheckConstraint("end_offset > start_offset"),
        CheckConstraint(f"state IN ({STALL_STATES})")))
feeding_daily = scoped("feeding_daily", C("run_id", UUID, nullable=False), C("camera_id", UUID, nullable=False),
    C("stall_key", String(40), nullable=False), C("day", Date, nullable=False),
    C("feeding_seconds", Float, nullable=False), C("not_feeding_seconds", Float, nullable=False),
    C("not_visible_seconds", Float, nullable=False), C("observed_seconds", Float, nullable=False),
    C("bout_count", Integer, nullable=False), C("mean_confidence", Float), C("origin", String(20), nullable=False),
    constraints=(ref("run_id", "runs"), ref("camera_id", "cameras"), UniqueConstraint("run_id", "stall_key", "day")))
Index("feeding_segments_lookup", feeding_segments.c.farm_id, feeding_segments.c.camera_id, feeding_segments.c.start_at)
Index("crop_labels_sample", crop_labels.c.sample_id, crop_labels.c.created_at)

# Migration 0003: BORIS gold sets for evaluation. Immutable once imported.
gold_sets = scoped("gold_sets", C("media_id", UUID, nullable=False), C("camera_id", UUID, nullable=False),
    C("source_name", String(200), nullable=False), C("checksum", String(64), nullable=False),
    C("mapping", JSONB, nullable=False), C("synthetic", Boolean, nullable=False), C("note", Text, nullable=False),
    constraints=(ref("media_id", "media"), ref("camera_id", "cameras")))
gold_events = scoped("gold_events", C("gold_set_id", UUID, nullable=False), C("stall_key", String(40), nullable=False),
    C("state", String(20), nullable=False), C("start_offset", Float, nullable=False), C("end_offset", Float, nullable=False),
    constraints=(ref("gold_set_id", "gold_sets"), CheckConstraint("end_offset > start_offset"),
        CheckConstraint(f"state IN ({STALL_STATES})")))
