import asyncio
import math
import httpx

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

HEADERS = {"User-Agent": "SprawdzDzialke.pl/1.0 (B2B land analysis)"}

# Kategorie POI - promień w metrach
KATEGORIE_POI = {
    "edukacja": {
        "promien": 2000,
        "filtry": [
            ("amenity", "school", "Szkoła"),
            ("amenity", "kindergarten", "Przedszkole"),
            ("amenity", "university", "Uczelnia"),
            ("amenity", "college", "Szkoła wyższa"),
        ]
    },
    "handel": {
        "promien": 1500,
        "filtry": [
            ("shop", "supermarket", "Supermarket"),
            ("shop", "convenience", "Sklep osiedlowy"),
            ("shop", "mall", "Galeria handlowa"),
            ("shop", "bakery", "Piekarnia"),
        ]
    },
    "zdrowie": {
        "promien": 3000,
        "filtry": [
            ("amenity", "pharmacy", "Apteka"),
            ("amenity", "hospital", "Szpital"),
            ("amenity", "clinic", "Przychodnia"),
            ("amenity", "doctors", "Gabinet lekarski"),
        ]
    },
    "transport": {
        "promien": 1500,
        "filtry": [
            ("highway", "bus_stop", "Przystanek autobusowy"),
            ("railway", "station", "Stacja kolejowa"),
            ("railway", "halt", "Przystanek kolejowy"),
            ("railway", "tram_stop", "Przystanek tramwajowy"),
            ("public_transport", "platform", "Peron"),
        ]
    },
    "gastronomia": {
        "promien": 1500,
        "filtry": [
            ("amenity", "restaurant", "Restauracja"),
            ("amenity", "cafe", "Kawiarnia"),
        ]
    },
    "rekreacja": {
        "promien": 2000,
        "filtry": [
            ("leisure", "park", "Park"),
            ("leisure", "playground", "Plac zabaw"),
            ("leisure", "sports_centre", "Obiekt sportowy"),
            ("leisure", "pitch", "Boisko"),
        ]
    },
}

# Obiekty uciążliwe: (klucz, wartosc, etykieta, promien_skanu_m, prog_alarmowy_m)
OBIEKTY_UCIAZLIWE = [
    ("man_made", "wastewater_plant", "Oczyszczalnia ścieków", 1000, 500),
    ("landuse", "landfill", "Wysypisko / składowisko odpadów", 2000, 1000),
    ("landuse", "industrial", "Teren przemysłowy", 1000, 200),
    ("landuse", "cemetery", "Cmentarz", 500, 100),
    ("power", "line", "Linia wysokiego napięcia", 500, 100),
    ("power", "substation", "Stacja transformatorowa", 500, 100),
    ("amenity", "fuel", "Stacja benzynowa", 500, 100),
    ("railway", "rail", "Tory kolejowe", 500, 100),
    ("highway", "motorway", "Autostrada", 1000, 300),
    ("highway", "trunk", "Droga ekspresowa / krajowa", 1000, 200),
    ("man_made", "chimney", "Komin przemysłowy", 1000, 500),
]

KLASY_DROG = {
    "motorway": "Autostrada",
    "trunk": "Droga ekspresowa / krajowa",
    "primary": "Droga krajowa / wojewódzka",
    "secondary": "Droga wojewódzka / powiatowa",
    "tertiary": "Droga powiatowa",
    "unclassified": "Droga gminna",
    "residential": "Droga osiedlowa",
    "service": "Droga wewnętrzna / serwisowa",
    "track": "Droga gruntowa / polna",
    "living_street": "Strefa zamieszkania",
    "pedestrian": "Deptak / ciąg pieszy",
    "footway": "Chodnik / ścieżka",
    "path": "Ścieżka",
}


def _haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return int(R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


def _zbuduj_zapytanie_poi(lat, lon):
    parts = []
    for cfg in KATEGORIE_POI.values():
        for klucz, wartosc, _ in cfg["filtry"]:
            parts.append(f'node["{klucz}"="{wartosc}"](around:{cfg["promien"]},{lat},{lon});')
            parts.append(f'way["{klucz}"="{wartosc}"](around:{cfg["promien"]},{lat},{lon});')
    for klucz, wartosc, _, promien, _ in OBIEKTY_UCIAZLIWE:
        parts.append(f'node["{klucz}"="{wartosc}"](around:{promien},{lat},{lon});')
        parts.append(f'way["{klucz}"="{wartosc}"](around:{promien},{lat},{lon});')
        parts.append(f'relation["{klucz}"="{wartosc}"](around:{promien},{lat},{lon});')
    return f"[out:json][timeout:25];({''.join(parts)});out center tags;"


def _zbuduj_zapytanie_drogi(lat, lon, promien=200):
    return f"""
[out:json][timeout:20];
(
  way["highway"](around:{promien},{lat},{lon});
);
out center tags geom;
"""


async def _wykonaj_overpass(query: str):
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=30.0) as client:
        for endpoint in OVERPASS_ENDPOINTS:
            try:
                resp = await client.post(endpoint, data={"data": query})
                if resp.status_code == 200:
                    return resp.json()
            except Exception:
                continue
        return None


def _kategoryzuj_poi(elementy, lat, lon):
    wynik = {kat: [] for kat in KATEGORIE_POI.keys()}

    for el in elementy:
        tags = el.get("tags", {})
        center = el.get("center") or {"lat": el.get("lat"), "lon": el.get("lon")}
        if not center or not center.get("lat"):
            continue

        dystans = _haversine_m(lat, lon, center["lat"], center["lon"])

        for kat, cfg in KATEGORIE_POI.items():
            dopasowane = False
            for klucz, wartosc, etykieta in cfg["filtry"]:
                if tags.get(klucz) == wartosc and dystans <= cfg["promien"]:
                    wynik[kat].append({
                        "typ": etykieta,
                        "nazwa": tags.get("name") or etykieta,
                        "odleglosc_m": dystans
                    })
                    dopasowane = True
                    break
            if dopasowane:
                break

    for kat in wynik:
        wynik[kat].sort(key=lambda x: x["odleglosc_m"])
        wynik[kat] = wynik[kat][:5]

    return wynik


def _wykryj_uciazliwe(elementy, lat, lon):
    znalezione = []
    alarmowe = []

    for el in elementy:
        tags = el.get("tags", {})
        center = el.get("center") or {"lat": el.get("lat"), "lon": el.get("lon")}
        if not center or not center.get("lat"):
            continue

        dystans = _haversine_m(lat, lon, center["lat"], center["lon"])

        for klucz, wartosc, etykieta, promien, prog_alarmowy in OBIEKTY_UCIAZLIWE:
            if tags.get(klucz) == wartosc and dystans <= promien:
                wpis = {
                    "typ": etykieta,
                    "nazwa": tags.get("name") or etykieta,
                    "odleglosc_m": dystans,
                    "alarmowy": dystans < prog_alarmowy
                }
                znalezione.append(wpis)
                if wpis["alarmowy"]:
                    alarmowe.append(wpis)
                break

    znalezione.sort(key=lambda x: x["odleglosc_m"])
    return znalezione[:10], alarmowe


def _analizuj_droge(dane_drog, lat, lon):
    if not dane_drog or "elements" not in dane_drog:
        return None

    najblizsza = None
    najmniejszy_dystans = float("inf")

    for el in dane_drog.get("elements", []):
        tags = el.get("tags", {})
        if "highway" not in tags:
            continue

        dystans_min = float("inf")
        for pt in el.get("geometry") or []:
            d = _haversine_m(lat, lon, pt["lat"], pt["lon"])
            if d < dystans_min:
                dystans_min = d

        if dystans_min == float("inf"):
            center = el.get("center")
            if center:
                dystans_min = _haversine_m(lat, lon, center["lat"], center["lon"])

        if dystans_min < najmniejszy_dystans:
            najmniejszy_dystans = dystans_min
            najblizsza = {
                "typ": tags["highway"],
                "klasa": KLASY_DROG.get(tags["highway"], tags["highway"]),
                "nazwa": tags.get("name"),
                "nawierzchnia": tags.get("surface"),
                "oswietlenie": tags.get("lit"),
                "odleglosc_m": int(dystans_min)
            }

    return najblizsza


def _generuj_opis(poi, droga, uciazliwe, alarmowe):
    if alarmowe:
        nazwy = ", ".join(f"{a['typ']} ({a['odleglosc_m']}m)" for a in alarmowe[:3])
        return "czerwony", f"W bezpośrednim sąsiedztwie wykryto obiekty potencjalnie uciążliwe: {nazwy}. Rekomendowana weryfikacja wpływu na wartość inwestycji."

    liczba_poi = sum(len(v) for v in poi.values())

    if droga is None:
        return "zolty", "Nie udało się zidentyfikować drogi przylegającej w OSM. Konieczna weryfikacja dostępu komunikacyjnego."

    if droga["odleglosc_m"] > 150:
        return "zolty", f"Najbliższa droga ({droga['klasa']}) znajduje się {droga['odleglosc_m']}m od punktu. Konieczna weryfikacja dojazdu."

    if liczba_poi < 3:
        return "zolty", "Otoczenie ubogie w punkty usługowe (szkoły, sklepy, transport). Lokalizacja może ograniczać atrakcyjność pod zabudowę mieszkaniową."

    return "zielony", f"Otoczenie zapewnia podstawową infrastrukturę ({liczba_poi} punktów usługowych w zasięgu). Najbliższa droga: {droga['klasa']} ({droga['odleglosc_m']}m)."


async def sprawdz_otoczenie_osm(lat: float, lon: float):
    try:
        dane_poi, dane_drog = await asyncio.gather(
            _wykonaj_overpass(_zbuduj_zapytanie_poi(lat, lon)),
            _wykonaj_overpass(_zbuduj_zapytanie_drogi(lat, lon)),
            return_exceptions=True
        )

        if isinstance(dane_poi, Exception) or dane_poi is None:
            return {
                "status": "zolty",
                "opis": "Nie udało się pobrać danych z OpenStreetMap. Serwer Overpass może być przeciążony.",
                "zrodlo": "OSM",
                "poi": {}, "droga": None, "obiekty_uciazliwe": []
            }

        elementy_poi = dane_poi.get("elements", [])
        poi = _kategoryzuj_poi(elementy_poi, lat, lon)
        uciazliwe, alarmowe = _wykryj_uciazliwe(elementy_poi, lat, lon)
        droga = _analizuj_droge(
            dane_drog if not isinstance(dane_drog, Exception) else None,
            lat, lon
        )
        status, opis = _generuj_opis(poi, droga, uciazliwe, alarmowe)

        return {
            "status": status,
            "opis": opis,
            "zrodlo": "OSM",
            "poi": poi,
            "droga": droga,
            "obiekty_uciazliwe": uciazliwe
        }

    except Exception as e:
        return {
            "status": "zolty",
            "opis": f"Błąd techniczny OSM: {e}",
            "zrodlo": "OSM",
            "poi": {}, "droga": None, "obiekty_uciazliwe": []
        }
