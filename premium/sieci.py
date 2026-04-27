import httpx
import re
from pyproj import Transformer

transformer_do_2180 = Transformer.from_crs("EPSG:4326", "EPSG:2180", always_xy=True)
KIUT_WMS_URL = "https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaUzbrojeniaTerenu"


async def sprawdz_uzbrojenie_terenu(lat: float, lon: float):
    x_2180, y_2180 = transformer_do_2180.transform(lon, lat)
    bbox = f"{x_2180-5},{y_2180-5},{x_2180+5},{y_2180+5}"
    warstwy = "przewod_wodociagowy,przewod_kanalizacyjny,przewod_gazowy,przewod_elektroenergetyczny,przewod_telekomunikacyjny"

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
            czysty_tekst = re.sub(r"<[^>]+>", " ", resp.text).lower().strip()

            if "exception" in czysty_tekst or "error" in czysty_tekst:
                return {
                    "status": "zolty",
                    "opis": "Błąd serwerów GUGiK. Wymagana weryfikacja ręczna.",
                    "zrodlo": "KIUT",
                    "wykryte_sieci": []
                }

            if not czysty_tekst or "no features" in czysty_tekst:
                return {
                    "status": "zolty",
                    "opis": "Nie wykryto sieci w promieniu 5 m albo brak tu pełnych danych cyfrowych. Potrzebna weryfikacja na mapie zasadniczej.",
                    "zrodlo": "KIUT",
                    "wykryte_sieci": []
                }

            wykryte = []

            if "wodociąg" in czysty_tekst or "wodociag" in czysty_tekst or "wodn" in czysty_tekst:
                wykryte.append("Wodociąg")
            if "kanaliza" in czysty_tekst or "sanitarn" in czysty_tekst:
                wykryte.append("Kanalizacja")
            if "gazow" in czysty_tekst or "gaz" in czysty_tekst:
                wykryte.append("Gaz")
            if "elektroenerget" in czysty_tekst or "prąd" in czysty_tekst or "prad" in czysty_tekst or "kabel" in czysty_tekst:
                wykryte.append("Prąd")
            # Telekomunikacja - światłowód, miedź, kabel telefoniczny.
            # UWAGA: "kabel" sam w sobie jest już wyłapywany przez warunek "Prąd" wyżej -
            # tutaj wymagamy bardziej specyficznych fraz, żeby nie podwajać tej samej
            # warstwy w wynikach. "telekom" pokrywa "telekomunikacyjny", "telekomunikacja".
            if "telekom" in czysty_tekst or "światłowód" in czysty_tekst or "swiatlowod" in czysty_tekst:
                wykryte.append("Telekomunikacja")

            wykryte = list(dict.fromkeys(wykryte))

            if "nie udostępnia danych opisowych" in czysty_tekst and not wykryte:
                return {
                    "status": "zielony",
                    "opis": "W pobliżu działki wykryto uzbrojenie terenu, ale baza nie udostępnia pełnych danych opisowych.",
                    "zrodlo": "KIUT",
                    "wykryte_sieci": []
                }

            if wykryte:
                return {
                    "status": "zielony",
                    "opis": f"W promieniu 5 m wykryto: {', '.join(wykryte)}. To nie gwarantuje możliwości przyłączenia, ale jest dobrym sygnałem infrastrukturalnym.",
                    "zrodlo": "KIUT",
                    "wykryte_sieci": wykryte
                }

            return {
                "status": "zielony",
                "opis": "W pobliżu działki wykryto uzbrojenie terenu, ale bez możliwości jednoznacznego rozpoznania rodzaju sieci.",
                "zrodlo": "KIUT",
                "wykryte_sieci": []
            }

        except Exception as e:
            return {
                "status": "zolty",
                "opis": f"Błąd techniczny bazy KIUT: {e}",
                "zrodlo": "KIUT",
                "wykryte_sieci": []
            }