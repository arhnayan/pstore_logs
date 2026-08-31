"""Shared FastAPI dependencies."""

from __future__ import annotations

from app.collector import Collector, EventBus
from app.db import Database

db = Database()
event_bus = EventBus()
collector = Collector(db, event_bus)
