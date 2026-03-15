import httpx
import re
from pyproj import Transformer

transformer_do_2180 = Transformer.from_crs("EPSG:4326", "EPSG:2180", always_xy=True)
KIUT_WMS_URL = "https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaUzbrojeniaTerenu"

async def sprawdz_uzbrojenie_terenu(lat: float, lon: float):
    x_2180, y_2180 = transformer_do_2180.transform(lon, lat)
    bbox = f"{x_2180-5},{y_2180-5},{x_2180+5},{y_2180+5}"
    warstwy = "przewod_wodociagowy,przewod_kanalizacyjny,przewod_gazowy,przewod_elektroenergetyczny"
    
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
            resp = await client.get(KIUT_WMS_URL, params=params, timeout=15.0)
            czysty_tekst = re.sub(r'<[^>]+>', ' ', resp.text).lower().strip()

            if "exception" in czysty_tekst or "error" in czysty_tekst:
                return {
                    "parametr": "Sieci Uzbrojenia Terenu",
                    "status": "zolty",
                    "opis": "Błąd serwerów GUGiK. Wymagana weryfikacja ręczna."
                }

            if not czysty_tekst or czysty_tekst == "" or "no features" in czysty_tekst:
                return {
                    "parametr": "Sieci Uzbrojenia Terenu",
                    "status": "zolty",
                    "opis": "Brak widocznych sieci w promieniu 5m (lub starostwo nie prowadzi w tym miejscu cyfrowej bazy). Konieczna weryfikacja na mapie zasadniczej."
                }

            if "nie udostępnia danych opisowych" in czysty_tekst:
                return {
                    "parametr": "Sieci Uzbrojenia Terenu",
                    "status": "zielony",
                    "opis": "Zlokalizowano sieci uzbrojenia terenu w bezpośrednim sąsiedztwie działki (do 5m). Dokładne zbadanie ich parametrów i warunków przyłączeniowych wykonujemy w ramach pogłębionej analizy eksperckiej."
                }

            wykryte = []
            if "wodociąg" in czysty_tekst or "wodociag" in czysty_tekst or "wodn" in czysty_tekst:
                wykryte.append("Wodociąg")
            if "kanaliza" in czysty_tekst or "sanitarn" in czysty_tekst:
                wykryte.append("Kanalizacja")
            if "gazow" in czysty_tekst or "gaz" in czysty_tekst:
                wykryte.append("Gaz")
            if "elektroenerget" in czysty_tekst or "prąd" in czysty_tekst or "kabel" in czysty_tekst:
                wykryte.append("Prąd")

            if wykryte:
                sieci_str = ", ".join(wykryte)
                return {
                    "parametr": "Sieci Uzbrojenia Terenu",
                    "status": "zielony",
                    "opis": f"W promieniu 5m zlokalizowano: {sieci_str}. UWAGA: Obecność sieci nie gwarantuje technicznej możliwości podłączenia."
                }
            else:
                return {
                    "parametr": "Sieci Uzbrojenia Terenu",
                    "status": "zielony",
                    "opis": "Zlokalizowano sieci uzbrojenia terenu w bezpośrednim sąsiedztwie działki (do 5m). Dokładne zbadanie ich parametrów i warunków przyłączeniowych wykonujemy w ramach pogłębionej analizy eksperckiej."
                }
                
        except Exception as e:
            return {
                "parametr": "Sieci Uzbrojenia Terenu",
                "status": "zolty",
                "opis": f"Błąd techniczny bazy: {e}"
            }