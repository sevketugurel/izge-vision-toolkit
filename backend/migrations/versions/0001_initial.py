"""Initial farm-scoped schema. Do not mutate this snapshot when adding future migrations."""
from alembic import op
from izge.schema_v1 import metadata
revision = "0001"
down_revision = None

def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    metadata.create_all(op.get_bind())
    op.execute("""CREATE FUNCTION reject_history_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Append-only history'; END; $$""")
    for table in ('annotations', 'reviews', 'time_maps', 'observations'):
        op.execute(f'''CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table}
                       FOR EACH ROW EXECUTE FUNCTION reject_history_mutation()''')

def downgrade():
    metadata.drop_all(op.get_bind())
    op.execute("DROP FUNCTION reject_history_mutation()")
