from sentinelsat import SentinelAPI
from datetime import date
from shapely.geometry import shape
import os
from config import settings
# -------------------------------------------------------
# Connexion à l'API Copernicus
# -------------------------------------------------------
def get_sentinel_api() -> SentinelAPI:
    return SentinelAPI(
        user=settings.COPERNICUS_USER,
        password=settings.COPERNICUS_PASSWORD,
        api_url='https://apihub.copernicus.eu/apihub'
    )
# -------------------------------------------------------
# Rechercher les images Sentinel-2 pour un site
# -------------------------------------------------------
def search_sentinel_images(
    site_geojson: dict,
    date_debut: str = "2024-11-01",
    date_fin: str = "2025-02-28",
    max_cloud: int = 20
) -> list:
    """
    Recherche les images Sentinel-2 disponibles pour un polygone donné.
    Args:
        site_geojson : geometry GeoJSON du site
        date_debut   : début période (YYYY-MM-DD)
        date_fin     : fin période (YYYY-MM-DD)
        max_cloud    : couverture nuageuse max en %
    Returns:
        Liste des produits Sentinel-2 disponibles
    """
    api = get_sentinel_api()
    # Convertir GeoJSON en WKT pour sentinelsat
    geometry = shape(site_geojson)
    footprint = geometry.wkt
    # Recherche
    products = api.query(
        area=footprint,
        date=(date_debut.replace("-", ""), date_fin.replace("-", "")),
        platformname='Sentinel-2',
        producttype='S2MSI2A',          # Niveau 2A = surface réfléchie
        cloudcoverpercentage=(0, max_cloud)
    )
    products_df = api.to_dataframe(products)
    if products_df.empty:
        return []
    # Trier par couverture nuageuse croissante
    products_df = products_df.sort_values('cloudcoverpercentage')
    return products_df[['title', 'uuid', 'cloudcoverpercentage', 'ingestiondate']].to_dict('records')
# -------------------------------------------------------
# Télécharger une image Sentinel-2
# -------------------------------------------------------
def download_sentinel_image(uuid: str, output_dir: str = "./data/images") -> str:
    """
    Télécharge une image Sentinel-2 par son UUID.
    Returns:
        Chemin du fichier téléchargé
    """
    api = get_sentinel_api()
    os.makedirs(output_dir, exist_ok=True)
    api.download(uuid, directory_path=output_dir)
    return output_dir