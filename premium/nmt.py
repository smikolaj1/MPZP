import asyncio
import httpx
from pyproj import Transformer

transformer_do_2180 = Transformer.from_crs("EPSG:4326", "EPSG:2180", always_xy=True)
NMT_URL = "https://services.gugik.gov.pl/nmt/"

# Odległość między punktami pomiarowymi. 25 m to kompromis: dla typowej działki
# 800 do 3000 m² próbki wpadają w jej obrębie lub tuż przy granicy, a nachylenie
# liczone na tym dystansie dobrze oddaje spadek istotny dla fundamentów.
DYSTANS_SAMPLING_M = 25


async def _pobierz_wysokosc(client: httpx.AsyncClient, x: float, y: float):
    """
    Zwraca wysokość n.p.m. w metrach dla punktu (x, y) w EPSG:2180.
    GUGiK NMT oddaje zwykły tekst typu "114.7" albo błąd.
    """
    try:
        resp = await client.get(
            NMT_URL,
            params={"request": "GetHByXY", "x": x, "y": y},
            timeout=10.0
        )
        if resp.status_code != 200:
            return None
        tekst = resp.text.strip()
        # Prawidłowa odpowiedź to pojedyncza liczba. Błąd serwera potrafi wrócić
        # HTML albo JSON, wtedy float() rzuci wyjątkiem.
        return float(tekst)
    except Exception:
        return None


async def sprawdz_nachylenie_terenu(lat: float, lon: float):
    x0, y0 = transformer_do_2180.transform(lon, lat)
    d = DYSTANS_SAMPLING_M

    # Próbujemy centrum i cztery kierunki (N, S, E, W). Pięć punktów wystarcza
    # do oszacowania maksymalnego spadku, więcej zapytań HTTP nie daje
    # proporcjonalnego zysku precyzji przy wstępnej analizie działki.
    punkty = [
        ("centrum", x0, y0),
        ("N", x0, y0 + d),
        ("S", x0, y0 - d),
        ("E", x0 + d, y0),
        ("W", x0 - d, y0),
    ]

    async with httpx.AsyncClient(follow_redirects=True) as client:
        wyniki = await asyncio.gather(
            *[_pobierz_wysokosc(client, x, y) for _, x, y in punkty],
            return_exceptions=True
        )

    wysokosci = {}
    for (nazwa, _, _), h in zip(punkty, wyniki):
        if isinstance(h, (int, float)):
            wysokosci[nazwa] = h

    # Bez minimum dwóch punktów nie policzymy różnicy.
    if len(wysokosci) < 2:
        return {
            "status": "zolty",
            "opis": "Nie udało się pobrać danych NMT z GUGiK. Wymagana weryfikacja nachylenia terenu na mapie topograficznej.",
            "zrodlo": "GUGiK NMT",
            "nachylenie_proc": None,
            "wysokosc_m": None,
            "roznica_wysokosci_m": None
        }

    h_min = min(wysokosci.values())
    h_max = max(wysokosci.values())
    roznica = h_max - h_min

    # Nachylenie liczymy konserwatywnie: największa różnica wysokości dzielona
    # przez DYSTANS_SAMPLING_M (dystans między sąsiadem a centrum). Dla pary
    # N-S albo E-W faktyczny dystans to 2*DYSTANS, ale zostawiamy /d świadomie,
    # bo lepiej pokazać deweloperowi spadek nieco zawyżony niż zaniżony.
    nachylenie_proc = round((roznica / d) * 100, 1)

    # Progi dla typowej zabudowy jednorodzinnej:
    # poniżej 5% standardowe fundamenty bez wpływu na koszt,
    # 5 do 10% odwodnienie i ewentualne wyrównanie terenu,
    # 10 do 15% wzmocnione fundamenty, schodkowanie, znaczące koszty ziemne,
    # powyżej 15% projekt specjalistyczny, tarasowanie, badania geotechniczne.
    if nachylenie_proc < 5:
        status = "zielony"
        opis = (
            f"Teren praktycznie płaski (ok. {nachylenie_proc}% nachylenia, "
            f"różnica wysokości {round(roznica, 1)} m w promieniu {d} m). "
            f"Standardowe warunki posadowienia budynku."
        )
    elif nachylenie_proc < 10:
        status = "zolty"
        opis = (
            f"Teren lekko nachylony (ok. {nachylenie_proc}%, różnica wysokości "
            f"{round(roznica, 1)} m w promieniu {d} m). Przy projektowaniu budynku "
            f"należy uwzględnić odwodnienie i ewentualne wyrównanie terenu."
        )
    elif nachylenie_proc < 15:
        status = "zolty"
        opis = (
            f"Teren wyraźnie pochyły (ok. {nachylenie_proc}%, różnica wysokości "
            f"{round(roznica, 1)} m w promieniu {d} m). Realizacja inwestycji będzie "
            f"wymagać wzmocnionych fundamentów, schodkowania lub tarasowania działki. "
            f"Koszty prac ziemnych znacząco wyższe niż na terenie płaskim."
        )
    else:
        status = "czerwony"
        opis = (
            f"Teren bardzo stromy (ok. {nachylenie_proc}%, różnica wysokości "
            f"{round(roznica, 1)} m w promieniu {d} m). Zabudowa zwykle wymaga "
            f"projektu konstrukcyjnego z uwzględnieniem spadku, tarasowania i "
            f"specjalistycznych rozwiązań fundamentowych. Przed zakupem wymagane "
            f"badania geotechniczne."
        )

    return {
        "status": status,
        "opis": opis,
        "zrodlo": "GUGiK NMT",
        "nachylenie_proc": nachylenie_proc,
        "wysokosc_m": round(wysokosci.get("centrum", h_min), 1),
        "roznica_wysokosci_m": round(roznica, 1),
        "punkty_pomiarowe": {k: round(v, 2) for k, v in wysokosci.items()}
    }
