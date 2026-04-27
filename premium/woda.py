import httpx
import re
from pyproj import Transformer

transformer_do_2180 = Transformer.from_crs("EPSG:4326", "EPSG:2180", always_xy=True)
ISOK_WMS_URL = "https://wody.isok.gov.pl/wss/INSPIRE/INSPIRE_NZ_HY_MZPMRP_WMS"


async def sprawdz_zagrozenie_powodziowe(lat: float, lon: float):
    x_2180, y_2180 = transformer_do_2180.transform(lon, lat)
    bbox = f"{x_2180-1},{y_2180-1},{x_2180+1},{y_2180+1}"
    warstwy = "NZ.Fluvial,NZ.RiskZone,NZ.ArtificialWaterBearingInfrastructure"

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
        "INFO_FORMAT": "text/plain"
    }

    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            resp = await client.get(ISOK_WMS_URL, params=params, timeout=15.0)
            tekst = resp.text.strip()

            if "Exception" in tekst or "Error" in tekst:
                return {
                    "status": "zolty",
                    "opis": "Błąd serwerów Wód Polskich. Wymagana weryfikacja ręczna.",
                    "zrodlo": "ISOK"
                }

            if not tekst or "no features" in tekst.lower():
                return {
                    "status": "zielony",
                    "opis": "Działka znajduje się poza wyznaczonymi strefami ryzyka i zagrożenia powodziowego.",
                    "zrodlo": "ISOK"
                }

            match_ryzyko = re.search(r"lor_qualitativevalue\s*=\s*(.+)", tekst)

            if match_ryzyko:
                poziom = match_ryzyko.group(1).strip().upper()
                return {
                    "status": "czerwony",
                    "opis": f"Wykryto ryzyko powodziowe: {poziom}. Przed zakupem lub inwestycją potrzebna jest dokładniejsza analiza hydrologiczna.",
                    "zrodlo": "ISOK",
                    "poziom_ryzyka": poziom
                }

            return {
                "status": "czerwony",
                "opis": "Działka znajduje się w obszarze zagrożenia powodziowego. Wymagana szczegółowa analiza przed dalszym procedowaniem.",
                "zrodlo": "ISOK"
            }

        except Exception as e:
            return {
                "status": "zolty",
                "opis": f"Błąd techniczny bazy ISOK: {e}",
                "zrodlo": "ISOK"
            }