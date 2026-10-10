from alembic import context
from sqlalchemy import create_engine, pool
from izge.config import DATABASE_URL
from izge.schema import metadata

def run():
    if context.is_offline_mode():
        context.configure(url=DATABASE_URL, target_metadata=metadata, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
    else:
        with create_engine(DATABASE_URL, poolclass=pool.NullPool).connect() as connection:
            context.configure(connection=connection, target_metadata=metadata)
            with context.begin_transaction():
                context.run_migrations()
run()
