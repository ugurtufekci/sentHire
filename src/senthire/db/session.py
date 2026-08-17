from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from senthire.config import get_settings


@lru_cache
def get_engine():
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def db_session() -> Iterator[Session]:
    """FastAPI dependency: commit on success, roll back on failure.

    The rollback is guarded because some failures happen *after* the transaction
    is durable — an after-commit hook raising, for example. Calling rollback() on
    an already-committed session raises its own error and buries the real one, so
    an outage in a downstream service would surface as an opaque SQLAlchemy
    complaint instead of the actual cause.
    """
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        try:
            session.rollback()
        except Exception:
            # The transaction was already resolved (an after-commit hook raised,
            # for instance). Nothing to undo, and the rollback's own error must
            # not replace the one the caller needs to see.
            pass
        raise
    finally:
        session.close()
