from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, JSON
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/palmci_db")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

class Base(DeclarativeBase):
    pass

# -------------------------------------------------------
# TABLE 1 — Stats NDVI par site + année
# -------------------------------------------------------
class AnalyseNDVI(Base):
    __tablename__ = "analyses_ndvi"

    id          = Column(Integer, primary_key=True, index=True)
    site_id     = Column(Integer, nullable=False)
    site_nom    = Column(String(50), nullable=False)
    annee       = Column(String(10), nullable=False)
    date_debut  = Column(String(20), nullable=False)
    date_fin    = Column(String(20), nullable=False)
    ndvi_moyen  = Column(Float)
    ndvi_min    = Column(Float)
    ndvi_max    = Column(Float)
    zone1_ha    = Column(Float)   # Stress sévère
    zone2_ha    = Column(Float)   # Stress modéré
    zone3_ha    = Column(Float)   # Végétation saine
    zone_num    = Column(Integer) # Zone dominante (1, 2 ou 3)
    zone_label  = Column(String(50))
    dose        = Column(String(30))
    couleur     = Column(String(10))
    action      = Column(Text)
    nb_images   = Column(Integer)
    date_calcul = Column(DateTime, default=datetime.utcnow)

# -------------------------------------------------------
# TABLE 2 — URLs images satellites
# -------------------------------------------------------
class ImageSatellite(Base):
    __tablename__ = "images_satellites"

    id           = Column(Integer, primary_key=True, index=True)
    site_id      = Column(Integer, nullable=False)
    site_nom     = Column(String(50), nullable=False)
    annee        = Column(String(10), nullable=False)
    date_debut   = Column(String(20), nullable=False)
    date_fin     = Column(String(20), nullable=False)
    url_rgb      = Column(Text)
    url_ndvi     = Column(Text)
    url_infrarouge = Column(Text)
    url_prescription = Column(Text)
    date_calcul  = Column(DateTime, default=datetime.utcnow)

# -------------------------------------------------------
# TABLE 3 — Prescriptions engrais
# -------------------------------------------------------
class Prescription(Base):
    __tablename__ = "prescriptions"

    id           = Column(Integer, primary_key=True, index=True)
    site_id      = Column(Integer, nullable=False)
    site_nom     = Column(String(50), nullable=False)
    annee        = Column(String(10), nullable=False)
    age_palmier  = Column(Integer)
    ndvi_moyen   = Column(Float)
    zone_num     = Column(Integer)
    zone_label   = Column(String(50))
    type_engrais = Column(String(100))
    dose         = Column(String(30))
    couleur      = Column(String(10))
    action       = Column(Text)
    date_calcul  = Column(DateTime, default=datetime.utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)
    print("✅ Tables PostgreSQL créées")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()