import os
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy import create_engine, String, JSON, Float, Integer, Boolean, Text, event, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, synonym
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
(ROOT / 'data').mkdir(exist_ok=True)

def now() -> str:
    return datetime.now(timezone.utc).isoformat()

class Base(DeclarativeBase):
    pass

class Vehicle(Base):
    __tablename__ = 'vehicles'
    vin: Mapped[str] = mapped_column(String(17), primary_key=True)
    first_seen: Mapped[str] = mapped_column(String, default=now)
    last_seen: Mapped[str] = mapped_column(String, default=now)
    watched: Mapped[bool] = mapped_column(Boolean, default=False)
    deal_score: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    best_price: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)

class Listing(Base):
    __tablename__ = 'listings'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    vin: Mapped[str] = mapped_column(String(17), index=True)
    source: Mapped[str] = mapped_column(String, index=True)
    url: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    first_seen: Mapped[str] = mapped_column(String, default=now)
    last_seen: Mapped[str] = mapped_column(String, default=now)
    missing: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default='ACTIVE')
    deal_score: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)

class Price(Base):
    __tablename__ = 'prices'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    listing_id: Mapped[str] = mapped_column(String, index=True)
    vin: Mapped[str] = mapped_column(String(17), index=True)
    amount: Mapped[float] = mapped_column(Float)
    observed: Mapped[str] = mapped_column(String, default=now)

class Source(Base):
    __tablename__ = 'sources'
    name: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String, default='IDLE')
    last_success: Mapped[str | None] = mapped_column(String, nullable=True)
    next_scan: Mapped[float] = mapped_column(Float, default=0.0)
    observations: Mapped[int] = mapped_column(Integer, default=0)
    scans: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default='')
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    budget_month: Mapped[str] = mapped_column(String, default='')

class Activity(Base):
    __tablename__ = 'activity'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created: Mapped[str] = mapped_column(String, default=now)
    message: Mapped[str] = mapped_column(Text)
    at = synonym('created')

class Setting(Base):
    __tablename__ = 'settings'
    id: Mapped[int] = mapped_column(primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)

class Hunt(Base):
    __tablename__ = 'hunts'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    started_at: Mapped[str] = mapped_column(String, default=now, index=True)
    ended_at: Mapped[str | None] = mapped_column(String, nullable=True)
    baseline: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)

class MarketAverage(Base):
    __tablename__ = 'market_averages'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    model: Mapped[str] = mapped_column(String, index=True)
    year: Mapped[int] = mapped_column(Integer, index=True)
    avg_price: Mapped[float] = mapped_column(Float)
    avg_mileage: Mapped[float] = mapped_column(Float)
    sample_count: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[str] = mapped_column(String, default=now)

class Notification(Base):
    __tablename__ = 'notifications'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    vin: Mapped[str] = mapped_column(String(17), index=True)
    listing_id: Mapped[str] = mapped_column(String, index=True)
    deal_score: Mapped[float] = mapped_column(Float)
    sent_at: Mapped[str] = mapped_column(String, default=now)
    message: Mapped[str] = mapped_column(Text)

engine = create_engine(os.getenv('DATABASE_URL', 'sqlite:///' + str(ROOT / 'data/hunter.db')))

if engine.dialect.name == 'sqlite':
    @event.listens_for(engine, 'connect')
    def sqlite_setup(conn, _):
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA busy_timeout=10000')

Session = sessionmaker(engine, expire_on_commit=False)

def init_db():
    Base.metadata.create_all(engine)
    with Session.begin() as s:
        default_sources = [
            'Tesla.com',
            'CarGurus',
            'Autotrader',
            'Cars.com',
            'Carvana',
            'TrueCar',
            'Autohub Texas',
            'Autos of Dallas',
            'MarketCheck'
        ]
        for src in default_sources:
            if not s.get(Source, src):
                s.add(Source(name=src))
        
        if not s.get(Setting, 1):
            s.add(Setting(id=1, data={
                'latitude': 33.0246,
                'longitude': -97.0712,
                'radius': 100,
                'max_price': 32000,
                'ideal_max': 28000,
                'preferred_mileage': 60000,
                'models': ['Model 3', 'Model Y', 'Model S', 'Model X', 'Cybertruck'],
                'require_hw4': False,
                'dealer_interval_minutes': 60,
                'api_interval_minutes': 300,
                'monthly_api_budget': 450,
                'fsd_monthly': 99.0,
                'ownership_months': 36,
                'zip_code': '75022',
                'telegram_alert_threshold': 70
            }))
        if not s.get(Setting, 2):
            s.add(Setting(id=2, data={}))

init_db()
