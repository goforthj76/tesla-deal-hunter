import os
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy import create_engine, String, JSON, Float, Integer, Boolean, Text, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, synonym
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
(ROOT / 'data').mkdir(exist_ok=True)
def now():
    return datetime.now(timezone.utc).isoformat()

class Base(DeclarativeBase): pass
class Vehicle(Base):
    __tablename__ = 'vehicles'
    vin: Mapped[str] = mapped_column(String(17), primary_key=True)
    first_seen: Mapped[str] = mapped_column(String, default=now)
    last_seen: Mapped[str] = mapped_column(String, default=now)
    watched: Mapped[bool] = mapped_column(Boolean, default=False)
    deal_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)

class Listing(Base):
    __tablename__ = 'listings'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    vin: Mapped[str] = mapped_column(String(17), index=True)
    source: Mapped[str] = mapped_column(String, index=True)
    url: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON)
    first_seen: Mapped[str] = mapped_column(String, default=now)
    last_seen: Mapped[str] = mapped_column(String, default=now)
    missing: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default='ACTIVE')

class Price(Base):
    __tablename__ = 'prices'
    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[str] = mapped_column(String, index=True)
    vin: Mapped[str] = mapped_column(String, index=True)
    amount: Mapped[float] = mapped_column(Float)
    observed: Mapped[str] = mapped_column(String, default=now)

class Source(Base):
    __tablename__ = 'sources'
    name: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String, default='WAITING')
    error: Mapped[str] = mapped_column(Text, default='')
    last_success: Mapped[str | None] = mapped_column(String, nullable=True)
    next_scan: Mapped[float] = mapped_column(Float, default=0)
    scans: Mapped[int] = mapped_column(Integer, default=0)
    observations: Mapped[int] = mapped_column(Integer, default=0)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    budget_month: Mapped[str] = mapped_column(String, default='')

class Activity(Base):
    __tablename__ = 'activity'
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[str] = mapped_column(String, default=now)
    message: Mapped[str] = mapped_column(Text)
    created = synonym('at')

class Setting(Base):
    __tablename__ = 'settings'
    id: Mapped[int] = mapped_column(primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)

engine = create_engine(os.getenv('DATABASE_URL', 'sqlite:///' + str(ROOT / 'data/hunter.db')))
if engine.dialect.name == 'sqlite':
    @event.listens_for(engine, 'connect')
    def sqlite_setup(conn, _):
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA busy_timeout=10000')
Session = sessionmaker(engine, expire_on_commit=False)


class Hunt(Base):
    __tablename__ = 'hunts'
    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[str] = mapped_column(String, default=now, index=True)
    ended_at: Mapped[str | None] = mapped_column(String, nullable=True)
    baseline: Mapped[dict] = mapped_column(JSON)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)


def migrate_legacy_database():
    if engine.dialect.name != 'sqlite': return
    from sqlalchemy import inspect
    inspector=inspect(engine)
    if 'activity' in inspector.get_table_names():
        columns={c['name'] for c in inspector.get_columns('activity')}
        if 'created' in columns and 'at' not in columns:
            with engine.begin() as conn:
                conn.exec_driver_sql('ALTER TABLE activity RENAME COLUMN created TO at')
