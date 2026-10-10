"""BORIS gold sets (immutable) for evaluation and training exclusion."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0003"
down_revision = "0002"
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
    scoped("gold_sets",
        sa.Column("media_id", UUID, nullable=False), sa.Column("camera_id", UUID, nullable=False),
        sa.Column("source_name", sa.String(200), nullable=False), sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("mapping", JSONB, nullable=False), sa.Column("synthetic", sa.Boolean, nullable=False),
        sa.Column("note", sa.Text, nullable=False),
        constraints=(ref("media_id", "media"), ref("camera_id", "cameras")))
    scoped("gold_events",
        sa.Column("gold_set_id", UUID, nullable=False), sa.Column("stall_key", sa.String(40), nullable=False),
        sa.Column("state", sa.String(20), nullable=False), sa.Column("start_offset", sa.Float, nullable=False),
        sa.Column("end_offset", sa.Float, nullable=False),
        constraints=(ref("gold_set_id", "gold_sets"), sa.CheckConstraint("end_offset > start_offset"),
            sa.CheckConstraint(f"state IN ({STATES})")))
    for table in ("gold_sets", "gold_events"):
        op.execute(f'''CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table}
                       FOR EACH ROW EXECUTE FUNCTION reject_history_mutation()''')


def downgrade():
    op.drop_table("gold_events")
    op.drop_table("gold_sets")
