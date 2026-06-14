"""Service layer: domain operations both the web and CLI reach through the API."""

from .unit_of_work import UnitOfWork


def greet(uow: UnitOfWork) -> str:
    rows = uow.query("SELECT message FROM greetings WHERE id = 1")
    return rows[0]["message"] if rows else "(no greeting)"
