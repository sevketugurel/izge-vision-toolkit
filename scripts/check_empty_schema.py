"""Assert that `alembic downgrade base` removed every application table and function."""
from sqlalchemy import create_engine, text
from izge.config import DATABASE_URL

with create_engine(DATABASE_URL).connect() as db:
    tables = db.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"
                             " AND tablename <> 'alembic_version'")).scalars().all()
    functions = db.scalar(text("SELECT count(*) FROM pg_proc WHERE proname='reject_history_mutation'"))
if tables or functions:
    raise SystemExit(f"Downgrade left objects behind: tables={tables} functions={functions}")
print("Downgrade base: uygulama şeması boş.")
