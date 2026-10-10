"""Stall-level feeding pipeline: camera config key, crop samples/labels, models, segments, daily.

Frozen DDL for this revision; later schema changes need a new migration.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0002"
down_revision = "0001"
STATES = "'yemde','yemde_degil','gorunmuyor'"


def scoped(name, *columns, constraints=()):
    op.create_table(name,
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("farm_id", UUID(as_uuid=True), sa.ForeignKey("farms.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        *columns, sa.UniqueConstraint("farm_id", "id"), *constraints)


def ref(column, target):
    return sa.ForeignKeyConstraint(["farm_id", column], [f"{target}.farm_id", f"{target}.id"])


def upgrade():
    op.add_column("cameras", sa.Column("config_key", sa.String(80)))
    scoped("crop_samples",
        sa.Column("media_id", UUID, nullable=False), sa.Column("camera_id", UUID, nullable=False),
        sa.Column("run_id", UUID, nullable=False), sa.Column("stall_key", sa.String(40), nullable=False),
        sa.Column("offset", sa.Float, nullable=False), sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("local_hour", sa.Integer, nullable=False), sa.Column("daynight", sa.String(6), nullable=False),
        sa.Column("crop_key", sa.String(160), nullable=False), sa.Column("context_key", sa.String(160), nullable=False),
        constraints=(ref("media_id", "media"), ref("camera_id", "cameras"), ref("run_id", "runs"),
            sa.UniqueConstraint("run_id", "stall_key", "offset"), sa.CheckConstraint("daynight IN ('gunduz','gece')")))
    scoped("crop_labels",
        sa.Column("sample_id", UUID, nullable=False), sa.Column("label", sa.String(20), nullable=False),
        sa.Column("labeler", sa.String(80), nullable=False), sa.Column("supersedes_id", UUID),
        sa.Column("origin", sa.String(20), nullable=False),
        constraints=(ref("sample_id", "crop_samples"), ref("supersedes_id", "crop_labels"),
            sa.CheckConstraint(f"label IN ({STATES},'skipped','retracted')")))
    scoped("feeding_models",
        sa.Column("version", sa.String(80), nullable=False), sa.Column("backbone", sa.String(120), nullable=False),
        sa.Column("classifier", sa.String(40), nullable=False), sa.Column("label_count", sa.Integer, nullable=False),
        sa.Column("class_counts", JSONB, nullable=False), sa.Column("metrics", JSONB, nullable=False),
        sa.Column("train_days", JSONB, nullable=False), sa.Column("excluded_days", JSONB, nullable=False),
        sa.Column("storage_key", sa.String(160), nullable=False),
        constraints=(sa.UniqueConstraint("farm_id", "version"),))
    scoped("feeding_segments",
        sa.Column("run_id", UUID, nullable=False), sa.Column("camera_id", UUID, nullable=False),
        sa.Column("stall_key", sa.String(40), nullable=False), sa.Column("state", sa.String(20), nullable=False),
        sa.Column("start_offset", sa.Float, nullable=False), sa.Column("end_offset", sa.Float, nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence", sa.Float), sa.Column("origin", sa.String(20), nullable=False),
        constraints=(ref("run_id", "runs"), ref("camera_id", "cameras"), sa.CheckConstraint("end_offset > start_offset"),
            sa.CheckConstraint(f"state IN ({STATES})")))
    scoped("feeding_daily",
        sa.Column("run_id", UUID, nullable=False), sa.Column("camera_id", UUID, nullable=False),
        sa.Column("stall_key", sa.String(40), nullable=False), sa.Column("day", sa.Date, nullable=False),
        sa.Column("feeding_seconds", sa.Float, nullable=False), sa.Column("not_feeding_seconds", sa.Float, nullable=False),
        sa.Column("not_visible_seconds", sa.Float, nullable=False), sa.Column("observed_seconds", sa.Float, nullable=False),
        sa.Column("bout_count", sa.Integer, nullable=False), sa.Column("mean_confidence", sa.Float),
        sa.Column("origin", sa.String(20), nullable=False),
        constraints=(ref("run_id", "runs"), ref("camera_id", "cameras"), sa.UniqueConstraint("run_id", "stall_key", "day")))
    op.create_index("feeding_segments_lookup", "feeding_segments", ["farm_id", "camera_id", "start_at"])
    op.create_index("crop_labels_sample", "crop_labels", ["sample_id", "created_at"])
    # Labels are append-only history: undo is a new 'retracted' record, never an UPDATE/DELETE.
    op.execute('''CREATE TRIGGER immutable_crop_labels BEFORE UPDATE OR DELETE ON crop_labels
                  FOR EACH ROW EXECUTE FUNCTION reject_history_mutation()''')


def downgrade():
    for table in ("feeding_daily", "feeding_segments", "feeding_models", "crop_labels", "crop_samples"):
        op.drop_table(table)
    op.drop_column("cameras", "config_key")
