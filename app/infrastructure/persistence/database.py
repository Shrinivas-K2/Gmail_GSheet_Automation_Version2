"""
persistence/database.py

Owns the database connection. Repositories ask it for sessions.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.infrastructure.persistence.orm import Base


class Database:

    def __init__(self, url: str):
        # pool_pre_ping: check the connection is alive before using it
        self.engine = create_engine(url, pool_pre_ping=True)
        # expire_on_commit=False: objects stay readable after commit
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def create_schema(self) -> None:
        """Create the tables that are missing (existing tables are left alone)."""
        Base.metadata.create_all(self.engine)


def aware(value: Optional[datetime]) -> Optional[datetime]:
    """SQLite returns naive datetimes; treat them as UTC. PostgreSQL is already aware."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
