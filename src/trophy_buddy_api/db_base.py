"""Shared SQLAlchemy registry for independently organized persistence modules."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
