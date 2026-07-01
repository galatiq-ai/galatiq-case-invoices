"""SQLAlchemy engine and session factory for the normalized schema.

Shares acme.db with ops_db and precedent_db — all pipeline data lives
in one SQLite file. WAL mode is configured via an event listener on the engine.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config import DB_PATH

n_engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
NSession = sessionmaker(bind=n_engine)
