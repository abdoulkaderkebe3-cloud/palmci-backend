from fastapi import FastAPI
import ee
import os
import requests
# ============================================
# Initialiser Google Earth Engine
# ============================================
ee.Initialize(project='paml-ci')
app = FastAPI(
    title="PALMCI NDVI API",
    description="Analyse satellitaire NDVI — Groupe SIFCA",
    version="1.0.0"
)
# ============================================
# Les 8 sites PALMCI
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
# ============================================
# Classifier NDVI → Zone prescription
# ============================================
def classify_zone(ndvi: float) -> dict:
    if ndvi < 0.35:
        return {
            "zone": 1,
            "label": "Stress sévère",
            "dose": "DOSE_MAX",
            "couleur": "#e74c3c",
            "action": "Intervention urgente — apport NPK renforcé"
        }
    elif ndvi < 0.55:
        return {
            "zone": 2,
            "label": "Stress modéré",
            "dose": "DOSE_STANDARD",
            "couleur": "#f39c12",
            "action": "Dose standard — surveillance mensuelle"
        }
    else:
            return {
        
            "zone": 3,
            "label": "Végétation saine",
            "dose": "DOSE_REDUITE",
            "couleur": "#27ae60",
            "action": "Réduire les apports — palmiers en bon état"
        }
# ============================================
# Prescription selon âge du palmier
# ============================================
def get_engrais_par_age(age: int, zone: int) -> dict:
    if age < 5:
        type_engrais = "NPK 15-15-15 (croissance)"
    elif age <= 20:
        type_engrais = "NPK 12-6-22 + MgO (production)"
    else:
        type_engrais = "KCl + MgSO4 (maintien)"
    doses = {1: "dose maximale", 2: "dose standard", 3: "dose réduite"}
    return {
        "age_palmier": age,
        "type_engrais": type_engrais,
        "dose": doses.get(zone, "dose standard")
    }
# ============================================
# ENDPOINT 1 — Racine
# ============================================
@app.get("/")
def root():
    return {
        "projet": "PALMCI NDVI Pipeline",
        "version": "1.0.0",
        "status": "running",
        "total_sites": len(SITES),
        "endpoints": [
            "/api/sites",
            "/api/sites/{site_id}",
            "/api/ndvi/{site_id}",
            "/api/ndvi",
            "/api/images/{site_id}",
            "/api/prescription/{site_id}",
            "/docs"
        ]
    }
# ============================================
# ENDPOINT 2 — Liste tous les sites
# ============================================
@app.get("/api/sites")
def get_sites():
    return {"total": len(SITES), "sites": SITES}
# ============================================
# ENDPOINT 3 — Un site par ID
# ============================================
@app.get("/api/sites/{site_id}")
def get_site(site_id: int):
    site = next((s for s in SITES if s["id"] == site_id), None)
    if not site:
        return {"erreur": f"Site {site_id} introuvable"}
    return site
# ============================================
# ENDPOINT 4 — NDVI d'un site
# ============================================
@app.get("/api/ndvi/{site_id}")
def get_ndvi(
    site_id: int,
    date_debut: str = "2024-11-01",
    date_fin: str = "2025-02-28"
):
    site = next((s for s in SITES if s["id"] == site_id), None)
    if not site:
        return {"erreur": f"Site {site_id} introuvable"}
    zone = ee.Geometry.Rectangle(site["bbox"])
    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterDate(date_debut, date_fin)
        .filterBounds(zone)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
    )
    nb_images = s2.size().getInfo()
    if nb_images == 0:
        return {"erreur": "Aucune image disponible", "site": site["nom"]}
    def add_ndvi(img):
        return img.addBands(
            img.normalizedDifference(["B8", "B4"]).rename("NDVI")
        )
    composite = s2.map(add_ndvi).select("NDVI").median().clip(zone)
    stats = composite.reduceRegion(
        reducer=ee.Reducer.mean()
            .combine(ee.Reducer.min(), sharedInputs=True)
            .combine(ee.Reducer.max(), sharedInputs=True),
        geometry=zone,
        scale=10,
        maxPixels=1e9
    ).getInfo()
    ndvi_moyen = round(stats.get("NDVI_mean", 0), 4)
    zone_info = classify_zone(ndvi_moyen)
    return {
        "site_id": site_id,
        "nom": site["nom"],
        "ndvi_moyen": ndvi_moyen,
        "ndvi_min": round(stats.get("NDVI_min", 0), 4),
        "ndvi_max": round(stats.get("NDVI_max", 0), 4),
        "nb_images": nb_images,
        "zone": zone_info["zone"],
        "label": zone_info["label"],
        "dose_prescrite": zone_info["dose"],
        "couleur": zone_info["couleur"],
        "action": zone_info["action"],
        "periode": f"{date_debut} → {date_fin}"
    }
# ============================================
# ENDPOINT 5 — NDVI tous les sites
# ============================================
@app.get("/api/ndvi")
def get_ndvi_all(
    date_debut: str = "2024-11-01",
    date_fin: str = "2025-02-28"
):
    results = []
    for site in SITES:
        try:
            zone = ee.Geometry.Rectangle(site["bbox"])
            s2 = (
                ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
                .filterDate(date_debut, date_fin)
                .filterBounds(zone)
                .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
            )
            nb_images = s2.size().getInfo()
            if nb_images == 0:
                results.append({"site_id": site["id"], "nom": site["nom"], "erreur": "Aucune image"})
                continue
            def add_ndvi(img):
                return img.addBands(img.normalizedDifference(["B8", "B4"]).rename("NDVI"))
            composite = s2.map(add_ndvi).select("NDVI").median().clip(zone)
            stats = composite.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=zone,
                scale=10,
                maxPixels=1e9
            ).getInfo()
            ndvi_moyen = round(stats.get("NDVI_mean", 0), 4)
            zone_info = classify_zone(ndvi_moyen)
            results.append({
                "site_id": site["id"],
                "nom": site["nom"],
                "ndvi_moyen": ndvi_moyen,
                "nb_images": nb_images,
                "zone": zone_info["zone"],
                "label": zone_info["label"],
                "dose_prescrite": zone_info["dose"],
                "couleur": zone_info["couleur"]
            })
        except Exception as e:
            results.append({"site_id": site["id"], "nom": site["nom"], "erreur": str(e)})
    return {"total": len(results), "resultats": results}
# ============================================
# ENDPOINT 6 — Images satellites d'un site
# ============================================
@app.get("/api/images/{site_id}")
def get_images(
    site_id: int,
    date_debut: str = "2024-11-01",
    date_fin: str = "2025-02-28"
):
    site = next((s for s in SITES if s["id"] == site_id), None)
    if not site:
        return {"erreur": f"Site {site_id} introuvable"}
    zone = ee.Geometry.Rectangle(site["bbox"])
    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterDate(date_debut, date_fin)
        .filterBounds(zone)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
    )
    nb_images = s2.size().getInfo()
    if nb_images == 0:
        return {"erreur": "Aucune image disponible"}
    composite = s2.median().clip(zone)
    # Image couleurs naturelles RGB
    url_rgb = composite.select(['B4', 'B3', 'B2']).getThumbURL({
        'min': 0, 'max': 3000,
        'dimensions': 512,
        'region': zone,
        'format': 'png'
    })
    # Image NDVI rouge → jaune → vert
    ndvi = composite.normalizedDifference(['B8', 'B4']).rename('NDVI')
    url_ndvi = ndvi.getThumbURL({
        'min': 0, 'max': 1,
        'dimensions': 512,
        'region': zone,
        'palette': ['red', 'yellow', 'green'],
        'format': 'png'
    })
    # Image infrarouge fausses couleurs
    url_infrarouge = composite.select(['B8', 'B4', 'B3']).getThumbURL({
        'min': 0, 'max': 3000,
        'dimensions': 512,
        'region': zone,
        'format': 'png'
    })
    return {
        "site_id": site_id,
        "nom": site["nom"],
        "nb_images": nb_images,
        "images": {
            "rgb": url_rgb,
            "ndvi": url_ndvi,
            "infrarouge": url_infrarouge
        },
        "periode": f"{date_debut} → {date_fin}"
    }
# ============================================
# ENDPOINT 7 — Prescription engrais
# ============================================
@app.get("/api/prescription/{site_id}")
def get_prescription(
    site_id: int,
    age_palmier: int = 10,
    date_debut: str = "2024-11-01",
    date_fin: str = "2025-02-28"
):
    site = next((s for s in SITES if s["id"] == site_id), None)
    if not site:
        return {"erreur": f"Site {site_id} introuvable"}
    zone = ee.Geometry.Rectangle(site["bbox"])
    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterDate(date_debut, date_fin)
        .filterBounds(zone)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
    )
    nb_images = s2.size().getInfo()
    if nb_images == 0:
        return {"erreur": "Aucune image disponible"}
    def add_ndvi(img):
        return img.addBands(img.normalizedDifference(["B8", "B4"]).rename("NDVI"))
    composite = s2.map(add_ndvi).select("NDVI").median().clip(zone)
    stats = composite.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=zone,
        scale=10,
        maxPixels=1e9
    ).getInfo()
    ndvi_moyen = round(stats.get("NDVI_mean", 0), 4)
    zone_info = classify_zone(ndvi_moyen)
    engrais = get_engrais_par_age(age_palmier, zone_info["zone"])
    return {
        "site_id": site_id,
        "nom": site["nom"],
        "ndvi_moyen": ndvi_moyen,
        "zone": zone_info["zone"],
        "label": zone_info["label"],
        "couleur": zone_info["couleur"],
        "action": zone_info["action"],
        "prescription": {
            "age_palmier": age_palmier,
            "type_engrais": engrais["type_engrais"],
            "dose": engrais["dose"]
        },
        "periode": f"{date_debut} → {date_fin}"
    }