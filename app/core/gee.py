import ee
from dotenv import load_dotenv
import os

load_dotenv()

def init_gee():
    """Authentification Google Earth Engine"""
    key_file = os.getenv("GEE_KEY_FILE")
    service_account = os.getenv("GEE_SERVICE_ACCOUNT")
    project = os.getenv("GEE_PROJECT")

    credentials = ee.ServiceAccountCredentials(
        email=service_account,
        key_file=key_file
    )
    ee.Initialize(credentials, project=project)


def compute_ndvi_gee(site: dict,
    date_debut: str = "2024-11-01",
    date_fin: str = "2025-02-28"
) -> dict:
    """Calcule NDVI pour un site via GEE"""

    init_gee()

    bbox = site["bbox"]
    zone = ee.Geometry.Rectangle(bbox)

    # Charger Sentinel-2
    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterDate(date_debut, date_fin)
        .filterBounds(zone)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
    )

    nb_images = s2.size().getInfo()

    if nb_images == 0:
        return {
            "site_id": site["id"],
            "nom": site["nom"],
            "erreur": "Aucune image disponible",
            "nb_images": 0
        }

    # Calcul NDVI
    def add_ndvi(img):
        ndvi = img.normalizedDifference(["B8", "B4"]).rename("NDVI")
        return img.addBands(ndvi)

    composite = s2.map(add_ndvi).select("NDVI").median().clip(zone)

    # Stats
    stats = composite.reduceRegion(
        reducer=ee.Reducer.mean()
            .combine(ee.Reducer.min(), sharedInputs=True)
            .combine(ee.Reducer.max(), sharedInputs=True),
        geometry=zone,
        scale=10,
        maxPixels=1e9
    ).getInfo()

    ndvi_moyen = round(stats.get("NDVI_mean", 0), 4)

    # Zone prescription
    from app.core.ndvi import classify_ndvi_zone
    zone_info = classify_ndvi_zone(ndvi_moyen)

    return {
        "site_id": site["id"],
        "nom": site["nom"],
        "ndvi_moyen": ndvi_moyen,
        "ndvi_min": round(stats.get("NDVI_min", 0), 4),
        "ndvi_max": round(stats.get("NDVI_max", 0), 4),
        "nb_images": nb_images,
        "zone_prescription": zone_info["zone"],
        "dose_prescrite": zone_info["dose_prescrite"],
        "label": zone_info["label"],
        "action": zone_info["action"],
        "periode": f"{date_debut} → {date_fin}"
    }