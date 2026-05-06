import httpx
import re
from bs4 import BeautifulSoup
from pyproj import Transformer

transformer_do_2180 = Transformer.from_crs("EPSG:4326", "EPSG:2180", always_xy=True)

GDOS_WMS_URL = "https://sdi.gdos.gov.pl/wms"

# Warstwy GDOŚ z podziałem na rygor ochrony
# (nazwa_warstwy, etykieta_dla_klienta, rygor)
# rygor: "scisly" | "umiarkowany" | "punktowy"
# Warstwy GDOŚ - nazwy zgodne z GetCapabilities WMS https://sdi.gdos.gov.pl/wms
WARSTWY_GDOS = [
    # Ścisła ochrona
    ("GDOS:ParkiNarodowe",              "Park Narodowy",                       "scisly"),
    ("GDOS:Rezerwaty",                  "Rezerwat przyrody",                   "scisly"),
    ("GDOS:ObszarySpecjalnejOchrony",   "Obszar Natura 2000 (ptasi - OSO)",    "scisly"),
    ("GDOS:SpecjalneObszaryOchrony",    "Obszar Natura 2000 (siedliskowy - SOO)", "scisly"),
    # Umiarkowana ochrona
    ("GDOS:ParkiKrajobrazowe",             "Park Krajobrazowy",                 "umiarkowany"),
    ("GDOS:ObszaryChronionegoKrajobrazu",  "Obszar Chronionego Krajobrazu",     "umiarkowany"),
    ("GDOS:ZespolyPrzyrodniczoKrajobrazowe", "Zespół Przyrodniczo-Krajobrazowy", "umiarkowany"),
    ("GDOS:UzytkiEkologiczne",             "Użytek ekologiczny",                "umiarkowany"),
    # Punktowe (strefa ochronna 15 m)
    ("GDOS:StanowiskaDokumentacyjne",   "Stanowisko dokumentacyjne",           "punktowy"),
    ("GDOS:PomnikiPrzyrody",            "Pomnik przyrody",                     "punktowy"),
]


async def _sprawdz_warstwe(client: httpx.AsyncClient, bbox: str, nazwa_warstwy: str):
    params = {
        "SERVICE": "WMS",
        "REQUEST": "GetFeatureInfo",
        "VERSION": "1.1.1",
        "LAYERS": nazwa_warstwy,
        "QUERY_LAYERS": nazwa_warstwy,
        "SRS": "EPSG:2180",
        "BBOX": bbox,
        "WIDTH": "10",
        "HEIGHT": "10",
        "X": "5",
        "Y": "5",
        "INFO_FORMAT": "text/html",
        "FEATURE_COUNT": "5"
    }

    try:
        resp = await client.get(GDOS_WMS_URL, params=params, timeout=15.0)
        if resp.status_code != 200:
            return None, None

        tekst = BeautifulSoup(resp.text, "html.parser").get_text(separator=" ", strip=True)

        # Usuwamy "boilerplate" odpowiedzi GeoServera – jeśli po usunięciu
        # nie zostaje realna treść, to znaczy, że w bboxie nie ma żadnego feature'a.
        boilerplate = [
            "Geoserver GetFeatureInfo output",
            "GetFeatureInfo output",
            "GetFeatureInfo results",
        ]
        tekst_czysty = tekst
        for b in boilerplate:
            tekst_czysty = tekst_czysty.replace(b, "")
        tekst_czysty = tekst_czysty.strip()

        tekst_lower = tekst_czysty.lower()

        blacklist = [
            "exception", "error", "no features", "forbidden",
            "could not find layer", "layer not found", "unknown layer",
            "nieznana warstwa", "invalidlayer", "invalid layer",
            "service exception", "wms server error",
            "mswms", "msloadgetmapparams",
            "o usłudze", "o usludze", "usługa wms", "usluga wms",
            "wybierz odpowiednią warstwę", "wybierz odpowiednia warstwe",
            "pozwalająca na przeglądanie", "pozwalajaca na przegladanie",
        ]
        if any(err in tekst_lower for err in blacklist):
            return None, None

        # Wymagamy realnej treści (atrybuty feature'a są zwykle dłuższe niż 30 znaków)
        if len(tekst_czysty) < 30:
            return None, None

        # Dodatkowo – realna odpowiedź z GDOŚ zawiera nazwę obszaru lub kod (PLH, PLB itp.)
        # Jeśli brak tego – uznajemy za pustą odpowiedź
        markery_realnej_odpowiedzi = [
            "nazwa", "kod", "plh", "plb", "powierzchnia",
            "data", "akt prawny", "id_", "park", "rezerwat",
            "chroniony", "natura", "pomnik", "użytek", "uzytek"
        ]
        if not any(m in tekst_lower for m in markery_realnej_odpowiedzi):
            return None, None

        import re as _re
        nazwa_obszaru = None
        match_nazwa = _re.search(r"nazwa[:\s]+([^\n|]+)", tekst_czysty, _re.IGNORECASE)
        if match_nazwa:
            nazwa_obszaru = match_nazwa.group(1).strip()[:120]

        return tekst_czysty, nazwa_obszaru

        nazwa_obszaru = None
        match_nazwa = re.search(r"nazwa[:\s]+([^\n|]+)", tekst, re.IGNORECASE)
        if match_nazwa:
            nazwa_obszaru = match_nazwa.group(1).strip()[:120]

        return tekst, nazwa_obszaru

    except Exception:
        return None, None


async def sprawdz_ochrone_przyrody(lat: float, lon: float):
    x_2180, y_2180 = transformer_do_2180.transform(lon, lat)
    bbox = f"{x_2180-2},{y_2180-2},{x_2180+2},{y_2180+2}"

    wykryte = []

    async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
        try:
            # Sekwencyjnie – GDOŚ potrafi się gubić przy wielu równoległych zapytaniach
            import asyncio
            zadania = [
                _sprawdz_warstwe(client, bbox, warstwa)
                for warstwa, _, _ in WARSTWY_GDOS
            ]
            wyniki = await asyncio.gather(*zadania, return_exceptions=True)

            for (warstwa, etykieta, rygor), wynik in zip(WARSTWY_GDOS, wyniki):
                if isinstance(wynik, Exception) or wynik == (None, None):
                    continue
                tekst, nazwa = wynik
                if not tekst:
                    continue
                wykryte.append({
                    "typ": etykieta,
                    "warstwa": warstwa,
                    "rygor": rygor,
                    "nazwa": nazwa,
                    "surowy_tekst": tekst[:400]
                })

        except Exception as e:
            return {
                "status": "zolty",
                "opis": f"Błąd techniczny bazy GDOŚ: {e}",
                "zrodlo": "GDOŚ",
                "formy_ochrony": []
            }

    ma_scisly = any(w["rygor"] == "scisly" for w in wykryte)
    ma_umiarkowany = any(w["rygor"] == "umiarkowany" for w in wykryte)
    ma_punktowy = any(w["rygor"] == "punktowy" for w in wykryte)

    if ma_scisly:
        status = "czerwony"
        nazwy = ", ".join(w["typ"] for w in wykryte if w["rygor"] == "scisly")
        opis = (
            f"Działka leży w obrębie form ochrony przyrody o najwyższym rygorze: {nazwy}. "
            f"Zabudowa może być istotnie ograniczona lub wymagać uzgodnień z RDOŚ/GDOŚ. "
            f"Konieczna szczegółowa analiza przed zakupem."
        )
    elif ma_umiarkowany:
        status = "zolty"
        nazwy = ", ".join(w["typ"] for w in wykryte if w["rygor"] == "umiarkowany")
        opis = (
            f"Działka w obszarze ograniczonej ochrony przyrody: {nazwy}. "
            f"Inwestycja możliwa, ale mogą wystąpić ograniczenia w zakresie zabudowy, "
            f"wysokości, kolorystyki lub konieczność uzgodnień."
        )
    elif ma_punktowy:
        status = "zolty"
        nazwy = ", ".join(w["typ"] for w in wykryte if w["rygor"] == "punktowy")
        opis = (
            f"W promieniu 15 m od punktu sprawdzenia wykryto punktową formę ochrony: {nazwy}. "
            f"Zgodnie z art. 45 ustawy o ochronie przyrody w strefie 15 m wokół pomnika obowiązuje "
            f"zakaz robót ziemnych i zabudowy – jednak ta strefa może zajmować tylko fragment działki. "
            f"Wymagana weryfikacja ręczna na mapie zasadniczej: jeśli strefa dotyczy brzegu działki, "
            f"pozostała część może być w pełni zabudowalna."
        )
    else:
        status = "zielony"
        opis = "Działka poza formami ochrony przyrody według GDOŚ."

    return {
        "status": status,
        "opis": opis,
        "zrodlo": "GDOŚ",
        "formy_ochrony": wykryte
    }