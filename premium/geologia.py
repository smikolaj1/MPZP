import httpx
from bs4 import BeautifulSoup
from pyproj import Transformer

transformer_do_2180 = Transformer.from_crs("EPSG:4326", "EPSG:2180", always_xy=True)
SOPO_WMS_URL = "https://cbdgmapa.pgi.gov.pl/arcgis/services/geozagrozenia/sopo_obszary/MapServer/WMSServer"


async def sprawdz_osuwiska(lat: float, lon: float):
    x_2180, y_2180 = transformer_do_2180.transform(lon, lat)
    bbox = f"{x_2180-1},{y_2180-1},{x_2180+1},{y_2180+1}"
    warstwy = "0,1,2,13"

    params = {
        "SERVICE": "WMS",
        "REQUEST": "GetFeatureInfo",
        "VERSION": "1.1.1",
        "LAYERS": warstwy,
        "QUERY_LAYERS": warstwy,
        "SRS": "EPSG:2180",
        "BBOX": bbox,
        "WIDTH": "10",
        "HEIGHT": "10",
        "X": "5",
        "Y": "5",
        "INFO_FORMAT": "text/html"
    }

    async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
        try:
            resp = await client.get(SOPO_WMS_URL, params=params, timeout=15.0)
            tekst = BeautifulSoup(resp.text, "html.parser").get_text(separator=" ", strip=True)
            tekst_lower = tekst.lower()

            if "serviceexception" in tekst_lower or "error" in tekst_lower:
                return {
                    "status": "zolty",
                    "opis": "Błąd serwerów geologicznych. Wymagana weryfikacja ręczna.",
                    "zrodlo": "SOPO"
                }

            if not tekst or "no features" in tekst_lower:
                return {
                    "status": "zielony",
                    "opis": "Działka znajduje się poza udokumentowanymi strefami osuwisk.",
                    "zrodlo": "SOPO"
                }

            aktywnosc = "nieznana"
            if "okresowo aktywne" in tekst_lower:
                aktywnosc = "okresowo aktywne"
            elif "nieaktywne" in tekst_lower:
                aktywnosc = "nieaktywne"
            elif "aktywne" in tekst_lower:
                aktywnosc = "aktywne"

            return {
                "status": "czerwony",
                "opis": f"Wykryto strefę osuwiskową. Status osuwiska: {aktywnosc}. Przed zakupem potrzebne są badania geotechniczne.",
                "zrodlo": "SOPO",
                "aktywnosc": aktywnosc
            }

        except Exception as e:
            return {
                "status": "zolty",
                "opis": f"Błąd techniczny bazy SOPO: {e}",
                "zrodlo": "SOPO"
            }