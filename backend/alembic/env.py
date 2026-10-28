from alembic import context

from app.db import engine, metadata

with engine.connect() as conn:
    context.configure(connection=conn, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()
