import numpy as np

def classify_ndvi_zone(ndvi_moyen: float) -> dict:
    if ndvi_moyen < 0.35:
        return {
            "zone": 1,
            "label": "Stress sévère",
            "couleur": "#e74c3c",
            "dose_prescrite": "DOSE_MAX",
            "action": "Intervention urgente — apport NPK renforcé"
        }
    elif ndvi_moyen < 0.55:
        return {
            "zone": 2,
            "label": "Stress modéré",
            "couleur": "#f39c12",
            "dose_prescrite": "DOSE_STANDARD",
            "action": "Dose standard — surveillance mensuelle"
        }
    else:
        return {
            "zone": 3,
            "label": "Végétation saine",
            "couleur": "#27ae60",
            "dose_prescrite": "DOSE_REDUITE",
            "action": "Réduire les apports — palmiers en bon état"
        }


def calculate_ndvi(red: float, nir: float) -> float:
    """
    Calcule le NDVI depuis les valeurs rouge et proche infrarouge.
    NDVI = (NIR - Rouge) / (NIR + Rouge)
    """
    if (nir + red) == 0:
        return 0.0
    return round((nir - red) / (nir + red), 4)