import json
import os
import base64
import requests
import io
from PIL import Image as PILImage
from dotenv import load_dotenv

load_dotenv()

# ============================================
# Recréer gee_key.json depuis variable d'env
# (nécessaire sur Render car fichier non commité)
# ============================================
gee_key_json = os.getenv("GEE_KEY_JSON")
gee_key_file = os.getenv("GEE_KEY_FILE", "./gee_key.json")

if gee_key_json and not os.path.exists(gee_key_file):
    with open(gee_key_file, "w") as f:
        f.write(gee_key_json)
    print("✅ gee_key.json recréé depuis GEE_KEY_JSON")

# ============================================
# Initialiser GEE avec Service Account
# ============================================
import ee

GEE_SERVICE_ACCOUNT = os.getenv("GEE_SERVICE_ACCOUNT", "palmci-gee@paml-ci.iam.gserviceaccount.com")

credentials = ee.ServiceAccountCredentials(GEE_SERVICE_ACCOUNT, gee_key_file)
ee.Initialize(credentials)
print("✅ GEE initialisé")

# ============================================
# Imports FastAPI
# ============================================
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import and_
from datetime import datetime, timedelta

from app.core.database import init_db, get_db, AnalyseNDVI, ImageSatellite, Prescription

# ============================================
# Init DB au démarrage
# ============================================
init_db()

# ============================================
# App FastAPI
# ============================================
app = FastAPI(
    title="PALMCI NDVI API",
    description="Analyse satellitaire NDVI — Groupe SIFCA",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
# Constantes
# ============================================
SITES = [
    {"id": 1, "nom": "EHANIA",     "bbox": [-3.092651, 5.197629, -2.933693, 5.368220]},
    {"id": 2, "nom": "TOUMANGUIE", "bbox": [-3.424644, 5.329935, -3.328514, 5.405818]},
    {"id": 3, "nom": "IROBO",      "bbox": [-4.872093, 5.288229, -4.703264, 5.388387]},
    {"id": 4, "nom": "BOUBO",      "bbox": [-5.320988, 5.628371, -5.283394, 5.672616]},
    {"id": 5, "nom": "BLIDOUBA",   "bbox": [-7.528725, 4.498564, -7.436714, 4.606455]},
    {"id": 6, "nom": "IBOKE",      "bbox": [-7.458172, 4.595204, -7.437229, 4.606027]},
    {"id": 7, "nom": "GBAPET",     "bbox": [-7.5611,   4.9223,   -7.4611,   5.0223  ]},
    {"id": 8, "nom": "NEKA",       "bbox": [-7.5626,   4.5156,   -7.4626,   4.6156  ]},
]

PERIODES = {
    "2023": {"debut": "2022-11-01", "fin": "2023-02-28"},
    "2024": {"debut": "2023-11-01", "fin": "2024-02-28"},
    "2025": {"debut": "2024-11-01", "fin": "2025-02-28"},
    "2026": {"debut": "2025-11-01", "fin": "2026-02-28"},
}

# ============================================
# Helpers
# ============================================
def classify_zone(ndvi: float) -> dict:
    if ndvi < 0.35:
        return {
            "zone": 1, "label": "Stress sévère",
            "dose": "DOSE_MAX", "couleur": "#e74c3c",
            "action": "Intervention urgente — apport NPK renforcé"
        }
    elif ndvi < 0.55:
        return {
            "zone": 2, "label": "Stress modéré",
            "dose": "DOSE_STANDARD", "couleur": "#f39c12",
            "action": "Dose standard — surveillance mensuelle"
        }
    else:
        return {
            "zone": 3, "label": "Végétation saine",
            "dose": "DOSE_REDUITE", "couleur": "#27ae60",
            "action": "Réduire les apports — palmiers en bon état"
        }

def get_engrais_par_age(age: int, zone: int) -> dict:
    if age < 5:
        type_engrais = "NPK 15-15-15 (croissance)"
    elif age <= 20:
        type_engrais = "NPK 12-6-22 + MgO (production)"
    else:
        type_engrais = "KCl + MgSO4 (maintien)"
    doses = {1: "dose maximale", 2: "dose standard", 3: "dose réduite"}
    return {"type_engrais": type_engrais, "dose": doses.get(zone, "dose standard")}

# ============================================
# Calcul GEE — NDVI complet
# ============================================
def calculer_ndvi_gee(site: dict, date_debut: str, date_fin: str) -> dict:
    zone = ee.Geometry.Rectangle(site["bbox"])

    # Fonction pour masquer les nuages et ombres via la bande SCL (Scene Classification)
    def mask_s2_clouds(image):
        scl = image.select('SCL')
        # 3: ombres de nuages, 8: nuages moyens, 9: nuages forts, 10: cirrus
        mask = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10))
        return image.updateMask(mask)

    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterDate(date_debut, date_fin)
        .filterBounds(zone)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 40)) # Plus tolérant vu qu'on masque
        .map(mask_s2_clouds)
    )
    nb_images = s2.size().getInfo()
    if nb_images == 0:
        return None

    composite = s2.map(lambda img: img.addBands(
        img.normalizedDifference(["B8", "B4"]).rename("NDVI")
    )).median().clip(zone)

    ndvi = composite.select("NDVI")

    # Stats NDVI (mean, min, max)
    stats = ndvi.reduceRegion(
        reducer=ee.Reducer.mean()
            .combine(ee.Reducer.min(), sharedInputs=True)
            .combine(ee.Reducer.max(), sharedInputs=True),
        geometry=zone, scale=10, maxPixels=1e9
    ).getInfo()

    # Zones prescription + superficies en hectares
    zones_img = ndvi.expression(
        "(ndvi < 0.40) ? 1 : (ndvi < 0.60) ? 2 : 3",
        {"ndvi": ndvi}
    ).rename("zone")
    pixel_area = ee.Image.pixelArea().divide(1e4)
    z1 = pixel_area.updateMask(zones_img.eq(1)).reduceRegion(
        reducer=ee.Reducer.sum(), geometry=zone, scale=10, maxPixels=1e9
    ).getInfo()
    z2 = pixel_area.updateMask(zones_img.eq(2)).reduceRegion(
        reducer=ee.Reducer.sum(), geometry=zone, scale=10, maxPixels=1e9
    ).getInfo()
    z3 = pixel_area.updateMask(zones_img.eq(3)).reduceRegion(
        reducer=ee.Reducer.sum(), geometry=zone, scale=10, maxPixels=1e9
    ).getInfo()

    ndvi_moyen = round(stats.get("NDVI_mean") or 0, 4)
    zone_info  = classify_zone(ndvi_moyen)

    return {
        "ndvi_moyen": ndvi_moyen,
        "ndvi_min":   round(stats.get("NDVI_min") or 0, 4),
        "ndvi_max":   round(stats.get("NDVI_max") or 0, 4),
        "zone1_ha":   round(z1.get("area") or 0, 2),
        "zone2_ha":   round(z2.get("area") or 0, 2),
        "zone3_ha":   round(z3.get("area") or 0, 2),
        "zone_num":   zone_info["zone"],
        "zone_label": zone_info["label"],
        "dose":       zone_info["dose"],
        "couleur":    zone_info["couleur"],
        "action":     zone_info["action"],
        "nb_images":  nb_images,
        "composite":  composite,
        "zone_img":   zones_img,
        "geometry":   zone,
    }

# ============================================
# Calcul GEE — Images satellites (Base64)
# ============================================
def url_to_base64(url: str) -> str:
    try:
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        # Recompression JPEG qualité 95 pour une qualité quasi-parfaite
        img = PILImage.open(io.BytesIO(response.content)).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode('utf-8')
    except Exception as e:
        print(f"Erreur téléchargement image GEE: {e}")
        return ""

def calculer_images_gee(composite, zones_img, zone) -> dict:
    url_rgb = composite.select(["B4", "B3", "B2"]).getThumbURL({
        "min": 0, "max": 3000, "dimensions": 512,
        "region": zone, "format": "jpg"
    })
    url_ndvi = composite.select("NDVI").getThumbURL({
        "min": 0, "max": 1, "dimensions": 512,
        "region": zone, "format": "jpg",
        "palette": ["red", "yellow", "green"]
    })
    url_infrarouge = composite.select(["B8", "B4", "B3"]).getThumbURL({
        "min": 0, "max": 4000, "dimensions": 512,
        "region": zone, "format": "jpg"
    })
    url_prescription = zones_img.getThumbURL({
        "min": 1, "max": 3, "dimensions": 512,
        "region": zone, "format": "jpg",
        "palette": ["#e74c3c", "#f39c12", "#27ae60"]
    })
    return {
        "url_rgb":          url_to_base64(url_rgb),
        "url_ndvi":         url_to_base64(url_ndvi),
        "url_infrarouge":   url_to_base64(url_infrarouge),
        "url_prescription": url_to_base64(url_prescription),
    }

# ============================================
# Sauvegarde DB — NDVI
# ============================================
def sauvegarder_ndvi(db: Session, site: dict, annee: str, date_debut: str, date_fin: str, data: dict):
    existing = db.query(AnalyseNDVI).filter(
        and_(AnalyseNDVI.site_id == site["id"], AnalyseNDVI.annee == annee)
    ).first()
    champs = {k: data[k] for k in [
        "ndvi_moyen", "ndvi_min", "ndvi_max",
        "zone1_ha", "zone2_ha", "zone3_ha",
        "zone_num", "zone_label", "dose",
        "couleur", "action", "nb_images"
    ]}
    if existing:
        for key, val in champs.items():
            setattr(existing, key, val)
        existing.date_calcul = datetime.utcnow()
    else:
        db.add(AnalyseNDVI(
            site_id=site["id"], site_nom=site["nom"],
            annee=annee, date_debut=date_debut, date_fin=date_fin,
            **champs
        ))
    db.commit()

# ============================================
# Sauvegarde DB — Images
# ============================================
def sauvegarder_images(db: Session, site: dict, annee: str, date_debut: str, date_fin: str, urls: dict):
    existing = db.query(ImageSatellite).filter(
        and_(ImageSatellite.site_id == site["id"], ImageSatellite.annee == annee)
    ).first()
    if existing:
        existing.url_rgb          = urls["url_rgb"]
        existing.url_ndvi         = urls["url_ndvi"]
        existing.url_infrarouge   = urls["url_infrarouge"]
        existing.url_prescription = urls["url_prescription"]
        existing.date_calcul      = datetime.utcnow()
    else:
        db.add(ImageSatellite(
            site_id=site["id"], site_nom=site["nom"],
            annee=annee, date_debut=date_debut, date_fin=date_fin,
            **urls
        ))
    db.commit()

# ============================================
# ENDPOINT — Racine
# ============================================
@app.get("/")
def root():
    return {
        "projet":  "PALMCI NDVI Pipeline",
        "version": "2.0.0",
        "status":  "running",
        "endpoints": [
            "/api/sites",
            "/api/analyse/{site_id}?annee=2025",
            "/api/images/{site_id}?annee=2025",
            "/api/prescription/{site_id}?annee=2025&age_palmier=10",
            "/api/sync",
            "/api/sync/{site_id}",
            "/docs",
        ]
    }

# ============================================
# ENDPOINT — Liste tous les sites
# ============================================
@app.get("/api/sites")
def get_sites():
    return {"total": len(SITES), "sites": SITES}

# ============================================
# ENDPOINT — Un site par ID
# ============================================
@app.get("/api/sites/{site_id}")
def get_site(site_id: int):
    site = next((s for s in SITES if s["id"] == site_id), None)
    if not site:
        return {"erreur": f"Site {site_id} introuvable"}
    return site

# ============================================
# ENDPOINT — Analyse NDVI d'un site
# Cache DB en priorité, sinon appel GEE
# ============================================
@app.get("/api/analyse/{site_id}")
def get_analyse(
    site_id: int,
    annee: str = "2025",
    db: Session = Depends(get_db)
):
    site = next((s for s in SITES if s["id"] == site_id), None)
    if not site:
        return {"erreur": f"Site {site_id} introuvable"}
    if annee not in PERIODES:
        return {"erreur": f"Année {annee} invalide. Choisir parmi : {list(PERIODES.keys())}"}

    # 1. Cache DB
    cached = db.query(AnalyseNDVI).filter(
        and_(AnalyseNDVI.site_id == site_id, AnalyseNDVI.annee == annee)
    ).first()

    if cached:
        return {
            "source":      "cache",
            "site_id":     cached.site_id,
            "nom":         cached.site_nom,
            "annee":       cached.annee,
            "ndvi_moyen":  cached.ndvi_moyen,
            "ndvi_min":    cached.ndvi_min,
            "ndvi_max":    cached.ndvi_max,
            "zone1_ha":    cached.zone1_ha,
            "zone2_ha":    cached.zone2_ha,
            "zone3_ha":    cached.zone3_ha,
            "zone":        cached.zone_num,
            "label":       cached.zone_label,
            "dose":        cached.dose,
            "couleur":     cached.couleur,
            "action":      cached.action,
            "nb_images":   cached.nb_images,
            "date_calcul": cached.date_calcul,
            "periode":     f"{cached.date_debut} → {cached.date_fin}"
        }

    # 2. GEE si pas en cache
    periode = PERIODES[annee]
    data = calculer_ndvi_gee(site, periode["debut"], periode["fin"])
    if not data:
        return {"erreur": "Aucune image satellite disponible", "site": site["nom"], "annee": annee}

    sauvegarder_ndvi(db, site, annee, periode["debut"], periode["fin"], data)

    return {
        "source":     "gee",
        "site_id":    site_id,
        "nom":        site["nom"],
        "annee":      annee,
        "ndvi_moyen": data["ndvi_moyen"],
        "ndvi_min":   data["ndvi_min"],
        "ndvi_max":   data["ndvi_max"],
        "zone1_ha":   data["zone1_ha"],
        "zone2_ha":   data["zone2_ha"],
        "zone3_ha":   data["zone3_ha"],
        "zone":       data["zone_num"],
        "label":      data["zone_label"],
        "dose":       data["dose"],
        "couleur":    data["couleur"],
        "action":     data["action"],
        "nb_images":  data["nb_images"],
        "periode":    f"{periode['debut']} → {periode['fin']}"
    }

# ============================================
# ENDPOINT — Images satellites d'un site
# Cache DB en priorité, sinon appel GEE
# ============================================
@app.get("/api/images/{site_id}")
def get_images(
    site_id: int,
    annee: str = "2025",
    db: Session = Depends(get_db)
):
    site = next((s for s in SITES if s["id"] == site_id), None)
    if not site:
        return {"erreur": f"Site {site_id} introuvable"}
    if annee not in PERIODES:
        return {"erreur": f"Année {annee} invalide"}

    # 1. Cache DB
    cached = db.query(ImageSatellite).filter(
        and_(ImageSatellite.site_id == site_id, ImageSatellite.annee == annee)
    ).first()

    if cached:
        # Invalider si ancienne URL GEE (expirée), encore PNG, ou JPEG basse qualité (avant le correctif HD)
        if cached.url_rgb and (
            not cached.url_rgb.startswith("data:image") or
            cached.url_rgb.startswith("data:image/png") or
            cached.date_calcul < datetime(2026, 5, 3, 2, 0)  # Forcer le passage en JPEG 95%
        ):
            cached = None
        else:
            return {
                "source":      "cache",
                "site_id":     site_id,
                "nom":         site["nom"],
                "annee":       annee,
                "images": {
                    "rgb":          cached.url_rgb,
                    "ndvi":         cached.url_ndvi,
                    "infrarouge":   cached.url_infrarouge,
                    "prescription": cached.url_prescription,
                },
                "date_calcul": cached.date_calcul,
            }

    # 2. GEE si pas en cache
    periode = PERIODES[annee]
    data = calculer_ndvi_gee(site, periode["debut"], periode["fin"])
    if not data:
        return {"erreur": "Aucune image disponible", "site": site["nom"], "annee": annee}

    urls = calculer_images_gee(data["composite"], data["zone_img"], data["geometry"])
    sauvegarder_images(db, site, annee, periode["debut"], periode["fin"], urls)

    return {
        "source":  "gee",
        "site_id": site_id,
        "nom":     site["nom"],
        "annee":   annee,
        "images": {
            "rgb":          urls["url_rgb"],
            "ndvi":         urls["url_ndvi"],
            "infrarouge":   urls["url_infrarouge"],
            "prescription": urls["url_prescription"],
        }
    }

# ============================================
# ENDPOINT — Prescription engrais
# ============================================
@app.get("/api/prescription/{site_id}")
def get_prescription(
    site_id: int,
    annee: str = "2025",
    age_palmier: int = 10,
    db: Session = Depends(get_db)
):
    site = next((s for s in SITES if s["id"] == site_id), None)
    if not site:
        return {"erreur": f"Site {site_id} introuvable"}

    # Cache DB
    cached = db.query(AnalyseNDVI).filter(
        and_(AnalyseNDVI.site_id == site_id, AnalyseNDVI.annee == annee)
    ).first()

    if cached:
        ndvi_moyen = cached.ndvi_moyen
        zone_num   = cached.zone_num
        zone_label = cached.zone_label
        couleur    = cached.couleur
        action     = cached.action
    else:
        periode = PERIODES.get(annee, PERIODES["2025"])
        data = calculer_ndvi_gee(site, periode["debut"], periode["fin"])
        if not data:
            return {"erreur": "Aucune image disponible"}
        sauvegarder_ndvi(db, site, annee, periode["debut"], periode["fin"], data)
        ndvi_moyen = data["ndvi_moyen"]
        zone_num   = data["zone_num"]
        zone_label = data["zone_label"]
        couleur    = data["couleur"]
        action     = data["action"]

    engrais = get_engrais_par_age(age_palmier, zone_num)

    return {
        "site_id":    site_id,
        "nom":        site["nom"],
        "annee":      annee,
        "ndvi_moyen": ndvi_moyen,
        "zone":       zone_num,
        "label":      zone_label,
        "couleur":    couleur,
        "action":     action,
        "prescription": {
            "age_palmier":  age_palmier,
            "type_engrais": engrais["type_engrais"],
            "dose":         engrais["dose"],
        }
    }

# ============================================
# ENDPOINT — Sync 1 site (toutes années)
# ============================================
@app.post("/api/sync/{site_id}")
def sync_site(site_id: int, db: Session = Depends(get_db)):
    site = next((s for s in SITES if s["id"] == site_id), None)
    if not site:
        return {"erreur": f"Site {site_id} introuvable"}

    resultats = []
    for annee, periode in PERIODES.items():
        try:
            data = calculer_ndvi_gee(site, periode["debut"], periode["fin"])
            if not data:
                resultats.append({"annee": annee, "status": "aucune image"})
                continue
            sauvegarder_ndvi(db, site, annee, periode["debut"], periode["fin"], data)
            urls = calculer_images_gee(data["composite"], data["zone_img"], data["geometry"])
            sauvegarder_images(db, site, annee, periode["debut"], periode["fin"], urls)
            resultats.append({"annee": annee, "status": "ok", "ndvi": data["ndvi_moyen"]})
        except Exception as e:
            resultats.append({"annee": annee, "status": "erreur", "detail": str(e)})

    return {"site": site["nom"], "resultats": resultats}

# ============================================
# ENDPOINT — Sync tous les sites toutes années
# ============================================
@app.post("/api/sync")
def sync_all(db: Session = Depends(get_db)):
    rapport = []
    for site in SITES:
        for annee, periode in PERIODES.items():
            try:
                data = calculer_ndvi_gee(site, periode["debut"], periode["fin"])
                if not data:
                    rapport.append({"site": site["nom"], "annee": annee, "status": "aucune image"})
                    continue
                sauvegarder_ndvi(db, site, annee, periode["debut"], periode["fin"], data)
                urls = calculer_images_gee(data["composite"], data["zone_img"], data["geometry"])
                sauvegarder_images(db, site, annee, periode["debut"], periode["fin"], urls)
                rapport.append({"site": site["nom"], "annee": annee, "status": "ok", "ndvi": data["ndvi_moyen"]})
            except Exception as e:
                rapport.append({"site": site["nom"], "annee": annee, "status": "erreur", "detail": str(e)})

    return {"total": len(rapport), "rapport": rapport}