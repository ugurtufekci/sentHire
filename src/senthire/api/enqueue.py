"""Dispatch background work only after the transaction that created it commits.

An API handler that calls ``task.delay(...)`` while its rows are still
uncommitted has published a pointer to data nobody else can see yet. A worker
that picks the message up first finds nothing and gives up, and the record sits
in its pending state forever — a race that reproduces rarely in development and
constantly under load, when workers are idle and the broker is fast.

Registering the dispatch on the session's ``after_commit`` event closes the
window: the message is published if and only if the data it refers to is
durable, and a rolled-back request enqueues nothing at all.
"""

from sqlalchemy import event
from sqlalchemy.orm import Session


def enqueue_after_commit(session: Session, task, *args, **kwargs) -> None:
    """Send `task` once `session`'s current transaction commits."""

    @event.listens_for(session, "after_commit", once=True)
    def _dispatch(_session: Session) -> None:
        task.delay(*args, **kwargs)
