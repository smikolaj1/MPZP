import httpx
from bs4 import BeautifulSoup
from pyproj import Transformer
from shapely import wkt as shapely_wkt
import geopandas as gpd
import io
import re
import warnings

warnings.filterwarnings("ignore", module="bs4")

ULDK_API_URL = "https://uldk.gugik.gov.pl/"
KIEG_WMS_URL = "https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaEwidencjiGruntow"
KIMPZP_WMS_URL = "https://mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaMiejscowychPlanowZagospodarowaniaPrzestrzennego"
KISKZP_WMS_URL = "https://mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaStudiumKierunkowZagospodarowaniaPrzestrzennego"
KIPOG_WMS_URL = "https://mapy.geoportal.gov.pl/wss/ext/ProjektowanePlanyOgolneGmin"

transformer_do_2180 = Transformer.from_crs("EPSG:4326", "EPSG:2180", always_xy=True)


async def pobierz_podstawy_dzialki(lat: float, lon: float):
    params_id = {"request": "GetParcelByXY", "xy": f"{lon},{lat},4326", "result": "id"}
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            resp = await client.get(ULDK_API_URL, params=params_id, timeout=10.0)
            linie = resp.text.strip().split('\n')
            if resp.status_code != 200 or len(linie) < 2 or linie[0] != "0":
                return {"error": "Nie znaleziono działki w tym punkcie."}

            wynik_id = linie[1].strip()

            # 1) Metadane administracyjne — zapytanie ZAWSZE bez geom_wkt
            #    (ULDK potrafi zwrócić błąd gdy w jednym `result` miesza się
            #    pola administracyjne z geometrią — trzymamy to osobno).
            params_details = {
                "request": "GetParcelById",
                "id": wynik_id,
                "result": "voivodeship,county,commune,region,parcel"
            }
            resp_details = await client.get(ULDK_API_URL, params=params_details, timeout=10.0)

            linie_detale = resp_details.text.strip().split('\n')
            if len(linie_detale) > 1 and linie_detale[0] == "0":
                # Pola administracyjne nie zawierają | ani , więc stary split działa,
                # ale używamy split("|") dla spójności i odporności.
                dane = linie_detale[1].split("|")
            else:
                dane = []

            # 2) Geometria działki — osobne zapytanie, graceful degradation.
            #    Jeśli padnie, raport działa dalej bez powierzchni i obrysu.
            powierzchnia_m2 = None
            powierzchnia_ha = None
            geom_wkt_czysty = None
            try:
                params_geom = {
                    "request": "GetParcelById",
                    "id": wynik_id,
                    "result": "geom_wkt"
                }
                resp_geom = await client.get(ULDK_API_URL, params=params_geom, timeout=10.0)
                linie_geom = resp_geom.text.strip().split('\n')
                if len(linie_geom) > 1 and linie_geom[0] == "0":
                    surowy_wkt = linie_geom[1].strip()
                    # ULDK zwraca "SRID=2180;POLYGON((...))" — obcinamy prefiks SRID.
                    # EPSG:2180 jest metryczny → shapely .area daje m² bez reprojekcji.
                    geom_wkt_czysty = (
                        surowy_wkt.split(";", 1)[1].strip()
                        if ";" in surowy_wkt else surowy_wkt
                    )
                    geom = shapely_wkt.loads(geom_wkt_czysty)
                    powierzchnia_m2 = round(geom.area, 1)
                    powierzchnia_ha = round(geom.area / 10000, 4)
            except Exception:
                # Brak geometrii nie blokuje raportu — po prostu pola zostają None.
                geom_wkt_czysty = None

            return {
                "id_dzialki": wynik_id,
                "wojewodztwo": dane[0] if len(dane) > 0 else "Brak",
                "powiat": dane[1] if len(dane) > 1 else "Brak",
                "gmina": dane[2] if len(dane) > 2 else "Brak",
                "obreb": dane[3] if len(dane) > 3 else "Brak",
                "numer_dzialki": dane[4] if len(dane) > 4 else "Brak",
                "powierzchnia_m2": powierzchnia_m2,
                "powierzchnia_ha": powierzchnia_ha,
                "geom_wkt": geom_wkt_czysty
            }
        except Exception as e:
            return {"error": f"Błąd połączenia z serwerem ULDK: {e}"}


async def sprawdz_klase_gruntu(lat: float, lon: float):
    x_2180, y_2180 = transformer_do_2180.transform(lon, lat)

    async def _pobierz(bufor: float):
        bbox = f"{x_2180-bufor},{y_2180-bufor},{x_2180+bufor},{y_2180+bufor}"
        params = {
            "SERVICE": "WMS",
            "REQUEST": "GetFeatureInfo",
            "VERSION": "1.1.1",
            "LAYERS": "kontury,uzytki",
            "QUERY_LAYERS": "kontury,uzytki",
            "SRS": "EPSG:2180",
            "BBOX": bbox,
            "WIDTH": "10",
            "HEIGHT": "10",
            "X": "5",
            "Y": "5",
            "INFO_FORMAT": "text/xml"
        }
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(KIEG_WMS_URL, params=params, timeout=15.0)
            return BeautifulSoup(resp.content, "xml")

    def _atr(soup, nazwa):
        tag = soup.find("Attribute", {"Name": nazwa})
        if not tag:
            return ""
        return (tag.text or "").strip()

    try:
        # Próba 1: bbox 5 m (tak jak dotychczas)
        soup = await _pobierz(5)

        grupa = _atr(soup, "Grupa rejestrowa")
        uzytek = _atr(soup, "Oznaczenie użytku")
        kontur = _atr(soup, "Oznaczenie konturu")
        pole_ha = _atr(soup, "Pole pow. w ewidencji gruntów (ha)")

        # Jeśli klasyfikacja pusta, spróbuj szerszym bbox (25 m) - bywa, że punkt trafia
        # między kontury i pikselowo dostaje pustą część warstwy klasyfikacyjnej.
        if not uzytek and not kontur:
            soup2 = await _pobierz(25)
            uzytek = _atr(soup2, "Oznaczenie użytku") or uzytek
            kontur = _atr(soup2, "Oznaczenie konturu") or kontur
            if not grupa:
                grupa = _atr(soup2, "Grupa rejestrowa")
            if not pole_ha:
                pole_ha = _atr(soup2, "Pole pow. w ewidencji gruntów (ha)")

        # Rozróżnienie trzech sytuacji:
        # A) pełne dane klasyfikacyjne → zielony
        # B) GUGiK odpowiedział, ale brak klasyfikacji dla tej działki → zolty (luka w EGiB)
        # C) żaden atrybut się nie wczytał → błąd serwera
        odpowiedz_zawiera_cokolwiek = any([grupa, uzytek, kontur, pole_ha])

        if uzytek or kontur:
            return {
                "surowy_wynik": "Dane XML (EPSG:2180)",
                "grupa_rejestrowa": grupa or "Brak",
                "klasa_gruntu": kontur or "Brak",
                "uzytek": f"Klasa: {kontur or '—'} | Użytek: {uzytek or '—'}",
                "pole_ha": pole_ha or None,
                "status_danych": "ok"
            }

        if odpowiedz_zawiera_cokolwiek:
            # EGiB odpowiedziało, ale klasyfikacja nie jest prowadzona dla tej działki
            # (typowo: działki zabudowane "B", albo brak aktualizacji przez starostę).
            return {
                "surowy_wynik": "Dane XML (EPSG:2180) - brak klasyfikacji",
                "grupa_rejestrowa": grupa or "Brak",
                "klasa_gruntu": "Brak danych w EGiB",
                "uzytek": "Dla tej działki publiczne EGiB nie udostępnia klasy gruntu ani oznaczenia użytku. Dane należy zweryfikować w wypisie z rejestru gruntów w starostwie.",
                "pole_ha": pole_ha or None,
                "status_danych": "luka_egib"
            }

        return {
            "grupa_rejestrowa": "Brak",
            "klasa_gruntu": "Brak",
            "uzytek": "Serwer WMS EGiB nie zwrócił odpowiedzi",
            "status_danych": "blad"
        }

    except Exception as e:
        return {
            "grupa_rejestrowa": "Brak",
            "klasa_gruntu": "Brak",
            "uzytek": f"Błąd serwera WMS: {e}",
            "status_danych": "blad"
        }


def _bbox_dla_punktu(lat: float, lon: float, bufor: float = 1.0):
    x_2180, y_2180 = transformer_do_2180.transform(lon, lat)
    return f"{x_2180-bufor},{y_2180-bufor},{x_2180+bufor},{y_2180+bufor}"


def _wyczysc_surowy_tekst(tekst: str) -> str:
    """Czyści surowy tekst z WMS: usuwa ciągi kropek, duplikaty spacji, ogony."""
    if not tekst:
        return ""
    # Sekwencje kropek (3+) → pojedyncza spacja. Bywa, że WMS kończy "................................"
    tekst = re.sub(r"\.{3,}", " ", tekst)
    # Powtórzenia "RysunekAktuPlanowania RysunekAktuPlanowania" → pojedyncze
    tekst = re.sub(r"\b(\w+)(\s+\1){1,}\b", r"\1", tekst)
    # Duplikaty spacji/tabów/nowych linii
    tekst = re.sub(r"\s+", " ", tekst).strip()
    # Obcinamy do 2000 znaków - nikt tego nie czyta, a JSON staje się czytelny
    return tekst[:2000]


def _czy_to_sensowna_odpowiedz(tekst: str) -> bool:
    if not tekst:
        return False

    tekst_lower = tekst.lower().strip()
    czarna_lista = [
        "302 found",
        "no layers",
        "exception",
        "forbidden",
        "brak serwisu",
        "nie obsługuje ramek",
        "bad request",
        "internal server error",
        "brak wyniku",
        "brak planu",
        "nie znaleziono",
        "invalid layer",
        "wms server error",
        "mswmsloadgetmapparams",
        "no features",
        # Strony informacyjne WMS (serwer zwraca "stronę startową" gdy brak danych).
        # UWAGA: frazy typu "dane o projektowanych planach", "wybierz odpowiednią warstwę"
        # NIE mogą być tutaj - KIPOG wrzuca je JAKO NAGŁÓWEK nawet gdy pod spodem są realne
        # dane POG. Ich obsługa jest w sprawdz_plan_ogolny przez strip boilerplate.
        "o usłudze",
        "o usludze",
        "usługa wms pozwalająca",
        "usluga wms pozwalajaca",
        "pozwalająca na przeglądanie",
        "pozwalajaca na przegladanie",
        "could not find layer",
        "layer not found",
    ]
    # wymagamy dłuższej treści - krótkie odpowiedzi z WMS to najczęściej błędy
    if len(tekst_lower) < 25:
        return False
    return not any(smiec in tekst_lower for smiec in czarna_lista)


def _wyciagnij_symbol_i_funkcje_z_tekstu(tekst: str):
    tekst_czysty = re.sub(r"\s+", " ", tekst).strip()
    tekst_lower = tekst_czysty.lower()

    # Zbiór "stopwords" – pojedyncze litery/skróty, które często pojawiają się w tekście,
    # ale nie są symbolami planistycznymi
    STOPWORDS_SYMBOLI = {
        "WMS", "XML", "HTML", "GML", "EPSG", "URL", "JST",
        "O", "A", "I", "W", "Z", "NA", "OD", "DO", "PO", "ZA",
        "GIS", "GUGIK", "RDOS", "GDOS", "JPG", "PNG", "PDF",
        "KB", "MB", "CM", "M", "KM", "SP", "LP", "NR",
        # Słowa-nagłówki z odpowiedzi WMS - nie są symbolami planistycznymi
        "NAZWA", "TYP", "DATA", "NUMER", "UCHWALA", "UCHWAŁY",
        "UCHWALENIA", "LINK", "MPZP", "POG", "STUDIUM", "RODZAJ",
        "ID", "KOD", "STATUS", "OPIS", "ROK", "RYSUNEK", "AKT",
        "AKTPLANOWANIA", "AKTU", "PLANOWANIA", "PLAN", "STREFA",
    }

    symbol = None

    # 1. Najbardziej wiarygodne – jawny tag "symbol_w_planie"
    match_symbol_plan = re.search(r"symbol_w_planie\s+([0-9]+\.[A-Z/]+)", tekst_czysty)
    if match_symbol_plan:
        symbol = match_symbol_plan.group(1)

    # 2. Fraza "obowiązuje X"
    if not symbol:
        match_status = re.search(r"\bobowiązuje\s+([A-Z]{2,}[A-Z/0-9]*)\b", tekst_czysty)
        if match_status:
            kandydat = match_status.group(1)
            if kandydat not in STOPWORDS_SYMBOLI:
                symbol = kandydat

        # Usuwamy numery uchwał typu "XV/181/2008", "Nr 123/2021" - żeby nie wyłapywać
    # cyfr rzymskich z nagłówków aktów prawnych jako symboli planistycznych
    tekst_bez_uchwal = re.sub(
        r"uchwa[łl]a\s*(?:nr\s*)?[IVXLCDM]+/\d+/\d+",
        "",
        tekst_czysty,
        flags=re.IGNORECASE
    )
    tekst_bez_uchwal = re.sub(
        r"\b[IVXLCDM]+/\d+/\d+\b",
        "",
        tekst_bez_uchwal
    )

    # 3. Fallback – szukaj symboli typu "1.MN", "MN/U", "MW"
    #    Wymagamy minimum 2 znaków dla symboli czysto literowych,
    #    żeby nie wyłapać przyimków typu "O", "W", "I".
    if not symbol:
        symbole = re.findall(r"\b([0-9]+\.[A-Z/]+|[A-Z]{2,6}(?:/[A-Z]{1,6})?)\b", tekst_bez_uchwal)

        # Wyklucz same cyfry rzymskie – to niemal zawsze numery sesji/uchwał, nie symbole planistyczne
        def _to_nie_cyfra_rzymska(s):
            return not bool(re.fullmatch(r"[IVXLCDM]+", s))

        symbole_odfiltrowane = [
            s for s in symbole
            if s not in STOPWORDS_SYMBOLI and _to_nie_cyfra_rzymska(s)
        ]
        symbol = symbole_odfiltrowane[0] if symbole_odfiltrowane else None

    # Funkcja – tylko gdy tekst wygląda na realną odpowiedź z planu,
    # nie stronę informacyjną serwisu
    funkcja = None

    strona_informacyjna_markery = [
        "o usłudze", "o usludze",
        "usługa wms", "usluga wms",
        "pozwalająca na przeglądanie", "pozwalajaca na przegladanie",
        "wybierz odpowiednią warstwę", "wybierz odpowiednia warstwe",
        "dane o projektowanych",
        "pozostałe informacje",
    ]
    if any(m in tekst_lower for m in strona_informacyjna_markery):
        return None, None

    # Dodatkowo – jeśli tekst nie zawiera typowych markerów planu,
    # nie zgaduj funkcji na podstawie pojedynczego słowa
    markery_planu = [
        "przeznaczenie", "teren", "zabudow", "strefa",
        "plan miejscowy", "plan ogólny", "plan ogolny",
        "miejscowy plan", "studium",
        "mpzp", "pog", "funkcja", "symbol"
    ]
    if not any(m in tekst_lower for m in markery_planu):
        return symbol, None

    if "wielorodzinnej i jednorodzinnej" in tekst_lower:
        funkcja = "Tereny zabudowy mieszkaniowej wielorodzinnej i jednorodzinnej"
    elif "jednorodzinnej lub / oraz wielorodzinnej" in tekst_lower:
        funkcja = "Tereny zabudowy mieszkaniowej jednorodzinnej lub wielorodzinnej"
    elif "wielorodzinnej" in tekst_lower and "jednorodzinnej" in tekst_lower:
        funkcja = "Tereny zabudowy mieszkaniowej wielorodzinnej i jednorodzinnej"
    elif "mieszkaniow" in tekst_lower:
        funkcja = "Tereny zabudowy mieszkaniowej"
    elif "usług" in tekst_lower or "uslug" in tekst_lower:
        funkcja = "Tereny usługowe"
    elif "produkcyjn" in tekst_lower:
        funkcja = "Tereny produkcyjne"
    elif "roln" in tekst_lower:
        funkcja = "Tereny rolne"
    elif "leśn" in tekst_lower or "lesn" in tekst_lower:
        funkcja = "Tereny leśne"
    elif "zieleni" in tekst_lower:
        funkcja = "Tereny zieleni"

    return symbol, funkcja

async def sprawdz_mpzp_dokument(lat: float, lon: float):
    bbox = _bbox_dla_punktu(lat, lon, bufor=1.0)
    warstwy = "wektor-pow,wektor-str,wektor-lzb,granice,raster,0,plan"

    params_bazowe = {
        "SERVICE": "WMS",
        "REQUEST": "GetFeatureInfo",
        "VERSION": "1.1.1",
        "LAYERS": warstwy,
        "QUERY_LAYERS": warstwy,
        "SRS": "EPSG:2180",
        "BBOX": bbox,
        "WIDTH": "3",
        "HEIGHT": "3",
        "X": "1",
        "Y": "1",
    }

    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            # 1. HTML
            params_html = params_bazowe.copy()
            params_html["INFO_FORMAT"] = "text/html"

            resp_html = await client.get(KIMPZP_WMS_URL, params=params_html, timeout=15.0)
            soup = BeautifulSoup(resp_html.text, "html.parser")
            tekst_html = soup.get_text(separator=" ", strip=True).replace("Geoserver GetFeatureInfo output", "").strip()

            if _czy_to_sensowna_odpowiedz(tekst_html):
                symbol, funkcja = _wyciagnij_symbol_i_funkcje_z_tekstu(tekst_html)
                meta = _wyciagnij_metadane_uchwaly(tekst_html)
                # Status zależy od tego czy udało nam się wyciągnąć przeznaczenie:
                # - symbol LUB funkcja → "zielony" (wiemy co wolno)
                # - tylko metadane aktu → "zolty" (wiemy że plan jest, ale nie co wolno)
                ma_dane_strefy = bool(symbol) or bool(funkcja)
                return {
                    "typ_dokumentu": "mpzp",
                    "zrodlo": "KIMPZP",
                    "status": "zielony" if ma_dane_strefy else "zolty",
                    "czy_wymaga_wz": False,
                    "symbol": symbol,
                    "nazwa_strefy": funkcja,
                    "numer_uchwaly": meta.get("numer_uchwaly"),
                    "data_uchwaly": meta.get("data_uchwaly"),
                    "data_obowiazywania_od": meta.get("data_obowiazywania_od"),
                    "status_aktu": meta.get("status_aktu"),
                    "nazwa_dokumentu": meta.get("nazwa_dokumentu"),
                    "opis": funkcja or (
                        "Znaleziono obowiązujący MPZP, ale publiczne WMS nie udostępnia "
                        "symbolu strefy. Przeznaczenie wymaga weryfikacji w rysunku planu."
                    ),
                    "surowy_tekst": _wyczysc_surowy_tekst(tekst_html),
                    "pewnosc": "wysoka" if ma_dane_strefy else "srednia",
                }

            # 2. GML
            params_gml = params_bazowe.copy()
            params_gml["INFO_FORMAT"] = "application/vnd.ogc.gml"

            resp_gml = await client.get(KIMPZP_WMS_URL, params=params_gml, timeout=20.0)

            try:
                with io.BytesIO(resp_gml.content) as f:
                    gdf = gpd.read_file(f)

                if not gdf.empty:
                    znalezione = []
                    for col in gdf.columns:
                        if any(k in col.lower() for k in ["przeznaczenie", "symbol", "nazwa", "funkcja", "kod"]):
                            wartosc = str(gdf.iloc[0][col])
                            if wartosc and wartosc.lower() != "none":
                                znalezione.append(f"{col}: {wartosc}")

                    if znalezione:
                        tekst_gml = " | ".join(znalezione)
                        symbol, funkcja = _wyciagnij_symbol_i_funkcje_z_tekstu(tekst_gml)
                        meta = _wyciagnij_metadane_uchwaly(tekst_gml)
                        ma_dane_strefy = bool(symbol) or bool(funkcja)
                        return {
                            "typ_dokumentu": "mpzp",
                            "zrodlo": "KIMPZP",
                            "status": "zielony" if ma_dane_strefy else "zolty",
                            "czy_wymaga_wz": False,
                            "symbol": symbol,
                            "nazwa_strefy": funkcja,
                            "numer_uchwaly": meta.get("numer_uchwaly"),
                            "data_uchwaly": meta.get("data_uchwaly"),
                            "data_obowiazywania_od": meta.get("data_obowiazywania_od"),
                            "status_aktu": meta.get("status_aktu"),
                            "nazwa_dokumentu": meta.get("nazwa_dokumentu"),
                            "opis": funkcja or (
                                "Znaleziono obowiązujący MPZP, ale publiczne WMS nie udostępnia "
                                "symbolu strefy. Przeznaczenie wymaga weryfikacji w rysunku planu."
                            ),
                            "surowy_tekst": _wyczysc_surowy_tekst(tekst_gml),
                            "pewnosc": "wysoka" if ma_dane_strefy else "srednia",
                        }
            except Exception:
                pass

            return None

        except Exception as e:
            return {
                "typ_dokumentu": "mpzp",
                "zrodlo": "KIMPZP",
                "status": "zolty",
                "czy_wymaga_wz": None,
                "symbol": None,
                "nazwa_strefy": None,
                "opis": f"Błąd techniczny podczas sprawdzania MPZP: {e}",
                "surowy_tekst": "",
                "pewnosc": "niska"
            }


async def sprawdz_plan_ogolny(lat: float, lon: float):
    bbox = _bbox_dla_punktu(lat, lon, bufor=1.0)
    warstwy_pog = "aktPlanowaniaprzestrzennego,obszarZabSrodmiejskiej,obszarUzupelnieniaZabudowy,strefaPlanistyczna"

    params_pog = {
        "SERVICE": "WMS",
        "REQUEST": "GetFeatureInfo",
        "VERSION": "1.1.1",
        "LAYERS": warstwy_pog,
        "QUERY_LAYERS": warstwy_pog,
        "SRS": "EPSG:2180",
        "BBOX": bbox,
        "WIDTH": "10",
        "HEIGHT": "10",
        "X": "5",
        "Y": "5",
        "TRANSPARENT": "TRUE",
        "INFO_FORMAT": "text/html"
    }

    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            resp_pog = await client.get(KIPOG_WMS_URL, params=params_pog, timeout=15.0)
            tekst_pog = BeautifulSoup(resp_pog.text, "html.parser").get_text(separator=" ", strip=True)

            # Usuwamy boilerplate serwerów - KIPOG ZAWSZE prefixuje odpowiedź nagłówkiem
            # "Dane o projektowanych planach ogólnych dla gmin. Dane dla wybranej gminy...",
            # nawet gdy pod spodem są realne dane POG. Traktujemy to jak ogonek Geoserver -
            # wycinamy, a decyzję 'czy sensowna odpowiedź' podejmujemy po oczyszczeniu.
            boilerplate_kipog = [
                "Geoserver GetFeatureInfo output",
                "Dane o projektowanych planach ogólnych dla gmin",
                "Dane o projektowanych planach ogolnych dla gmin",
                "Dane dla wybranej gminy (wybierz odpowiednią warstwę aby sprawdzić dostępne dane)",
                "Dane dla wybranej gminy (wybierz odpowiednia warstwe aby sprawdzic dostepne dane)",
                "Dane dla wybranej gminy",
                "wybierz odpowiednią warstwę aby sprawdzić dostępne dane",
                "wybierz odpowiednia warstwe aby sprawdzic dostepne dane",
                "Pozostałe informacje",
                "Pozostale informacje",
            ]
            for bp in boilerplate_kipog:
                tekst_pog = tekst_pog.replace(bp, "")
            # KIPOG dopisuje NA KOŃCU odpowiedzi stopkę "O usłudze Usługa WMS pozwalająca
            # na przeglądanie danych...". Te frazy trafiają do blacklisty jako "strona
            # informacyjna", wycinając całą sensowną odpowiedź POG. Wycinamy stopkę tutaj.
            tekst_pog = re.sub(
                r"\s*O\s+us[łl]udze\s+Us[łl]uga\s+WMS.*$",
                "",
                tekst_pog,
                flags=re.IGNORECASE,
            )
            tekst_pog = re.sub(r"\s+", " ", tekst_pog).strip()

            if not _czy_to_sensowna_odpowiedz(tekst_pog):
                return None

            symbol, funkcja = _wyciagnij_symbol_i_funkcje_z_tekstu(tekst_pog)
            meta = _wyciagnij_metadane_uchwaly(tekst_pog)
            parametry = _wyciagnij_parametry_pog(tekst_pog)

            # Status zależy od tego czy wyciągnęliśmy przeznaczenie GŁÓWNE strefy,
            # NIE od tego co pada w dopuszczeniach (w 'Profil podstawowy' SJ wymienione
            # jest m.in. "teren zieleni urządzonej" jako jedno z DOPUSZCZEŃ - to nie znaczy
            # że cała strefa to zieleń). Decyzja czerwony/zielony na podstawie SYMBOLU
            # i NAZWY strefy (a nie surowego tekstu).
            ma_dane_strefy = bool(symbol) or bool(funkcja)
            symbol_upper = (symbol or "").upper()
            funkcja_lower = (funkcja or "").lower()

            # Symbole planistyczne oznaczające strefy niebudowlane jako główne przeznaczenie:
            # ZP = zieleń urządzona, ZL/LS = lasy, ZN = zieleń naturalna, R = rolne,
            # WS = wody, ZC = cmentarze
            symbole_niebudowlane = ("ZP", "ZL", "LS", "ZN", "R", "WS", "ZC")
            nazwa_niebudowlana = any(
                fraza in funkcja_lower
                for fraza in ["tereny zieleni", "tereny leśne", "tereny lesne", "tereny rolne"]
            )

            if any(symbol_upper.startswith(s) for s in symbole_niebudowlane) or nazwa_niebudowlana:
                status = "czerwony"
            elif ma_dane_strefy:
                status = "zielony"
            else:
                status = "zolty"

            # Opis kontekstowy zależny od statusu aktu - "w opracowaniu" znaczy
            # że plan jeszcze nie obowiązuje, można go wykorzystać tylko POMOCNICZO.
            status_aktu = meta.get("status_aktu")
            if status_aktu == "w opracowaniu":
                opis_pog = (
                    "Dla działki PROJEKTOWANY jest Plan Ogólny Gminy (jeszcze nie obowiązuje). "
                    "Parametry strefy są wstępne i mogą ulec zmianie przed uchwaleniem. "
                    "Do realizacji inwestycji może być wymagana decyzja o Warunkach Zabudowy (WZ)."
                )
            else:
                opis_pog = (
                    "Obszar objęty Planem Ogólnym Gminy. Do realizacji inwestycji może być "
                    "wymagana decyzja o Warunkach Zabudowy (WZ)."
                )

            return {
                "typ_dokumentu": "plan_ogolny",
                "zrodlo": "KIPOG",
                "status": status,
                "czy_wymaga_wz": True,
                "symbol": symbol,
                "nazwa_strefy": funkcja,
                "numer_uchwaly": meta.get("numer_uchwaly"),
                "data_uchwaly": meta.get("data_uchwaly"),
                "data_obowiazywania_od": meta.get("data_obowiazywania_od"),
                "status_aktu": status_aktu,
                "nazwa_dokumentu": meta.get("nazwa_dokumentu"),
                "parametry": parametry,
                "opis": opis_pog,
                "surowy_tekst": _wyczysc_surowy_tekst(tekst_pog),
                "pewnosc": "srednia"
            }
        except Exception as e:
            return {
                "typ_dokumentu": "plan_ogolny",
                "zrodlo": "KIPOG",
                "status": "zolty",
                "czy_wymaga_wz": None,
                "symbol": None,
                "nazwa_strefy": None,
                "opis": f"Błąd techniczny podczas sprawdzania Planu Ogólnego: {e}",
                "surowy_tekst": "",
                "pewnosc": "niska"
            }


def _wyciagnij_parametry_pog(tekst: str) -> dict:
    """
    Wyciąga z surowego tekstu POG konkretne parametry zabudowy dla strefy.
    Dla dewelopera to NAJWAŻNIEJSZE dane - mówią dosłownie "co wolno zbudować":
    - maksymalna wysokość zabudowy (metry)
    - maksymalna intensywność zabudowy (stosunek powierzchni netto budynków do działki)
    - maksymalny % powierzchni zabudowy (ile działki wolno zabudować)
    - minimalny % powierzchni biologicznie czynnej (ile musi zostać zielone)
    - oznaczenie strefy (np. "40SJ")
    - profil podstawowy (lista dopuszczonych funkcji)
    - profil dodatkowy (lista uzupełniająca)
    """
    parametry = {
        "oznaczenie": None,
        "max_wysokosc_m": None,
        "max_intensywnosc": None,
        "max_pow_zabudowy_proc": None,
        "min_pow_biologicznie_czynnej_proc": None,
        "profil_podstawowy": [],
        "profil_dodatkowy": [],
    }

    if not tekst:
        return parametry

    tekst_czysty = re.sub(r"\s+", " ", tekst).strip()

    def _liczba_po_frazie(fraza_regex: str):
        m = re.search(fraza_regex + r"\s*(\d+(?:[.,]\d+)?)", tekst_czysty, flags=re.IGNORECASE)
        if m:
            try:
                return float(m.group(1).replace(",", "."))
            except ValueError:
                return None
        return None

    parametry["max_wysokosc_m"] = _liczba_po_frazie(r"maksymalna\s+wysoko[śs][ćc]\s+zabudowy")
    parametry["max_intensywnosc"] = _liczba_po_frazie(r"maksymalna\s+(?:nadziemna\s+)?intensywno[śs][ćc]\s+zabudowy")
    parametry["max_pow_zabudowy_proc"] = _liczba_po_frazie(r"maksymalny\s+udzia[łl]\s+powierzchni\s+zabudowy")
    parametry["min_pow_biologicznie_czynnej_proc"] = _liczba_po_frazie(
        r"minimalny\s+udzia[łl]\s+powierzchni\s+biologicznie\s+czynn[ea][jy]?"
    )

    # Oznaczenie - np. "Oznaczenie 40SJ"
    m_ozn = re.search(r"\bOznaczenie\s+([0-9]+[A-Z]+)\b", tekst_czysty)
    if m_ozn:
        parametry["oznaczenie"] = m_ozn.group(1)

    # Profile - lista po przecinkach, kończy się gdy pojawia się kolejne "pole: wartość"
    # Bierzemy greedy do następnego markera: "Profil dodatkowy", "Maksymalna", "Minimalny",
    # "Status", "Charakter", "Przestrzeń".
    stop_markers = r"(?=\s+(?:Profil\s+dodatkowy|Profil\s+podstawowy|Maksymaln|Minimaln|Status|Charakter|Przestrze[ńn]|Nazwa|Symbol|Oznaczenie|Obowi[ąa]zuje|Lokalny|Wersja|Pocz[ąa]tek|Koniec|$))"

    m_pp = re.search(
        r"Profil\s+podstawowy\s+(.+?)" + stop_markers,
        tekst_czysty,
        flags=re.IGNORECASE
    )
    if m_pp:
        surowa = m_pp.group(1).strip()
        if surowa and surowa != "-":
            parametry["profil_podstawowy"] = [
                p.strip() for p in surowa.split(",") if p.strip() and p.strip() != "-"
            ]

    m_pd = re.search(
        r"Profil\s+dodatkowy\s+(.+?)" + stop_markers,
        tekst_czysty,
        flags=re.IGNORECASE
    )
    if m_pd:
        surowa = m_pd.group(1).strip()
        if surowa and surowa != "-":
            parametry["profil_dodatkowy"] = [
                p.strip() for p in surowa.split(",") if p.strip() and p.strip() != "-"
            ]

    return parametry


def _wyciagnij_metadane_uchwaly(tekst: str) -> dict:
    """
    Wyciąga z surowego tekstu metadane aktu prawnego:
    - numer uchwały (np. "XV/181/2008")
    - data uchwalenia (TYLKO gdy występuje obok "uchwał..." - NIE mylić z "Obowiązuje od")
    - data obowiązywania od (z "Obowiązuje od X")
    - status aktu ("obowiązujący" / "w opracowaniu")
    - nazwa dokumentu
    """
    meta = {
        "numer_uchwaly": None,
        "data_uchwaly": None,
        "data_obowiazywania_od": None,
        "status_aktu": None,
        "nazwa_dokumentu": None,
    }

    if not tekst:
        return meta

    tekst_czysty = re.sub(r"\s+", " ", tekst).strip()
    tekst_lower = tekst_czysty.lower()

    # Numer uchwały – typowy format: "XV/181/2008"
    match_numer = re.search(
        r"uchwa[łl]a\s*(?:nr\s*)?([IVXLCDM]+/\d+/\d{2,4})",
        tekst_czysty,
        flags=re.IGNORECASE
    )
    if not match_numer:
        match_numer = re.search(r"\b([IVXLCDM]+/\d+/\d{2,4})\b", tekst_czysty)
    if match_numer:
        meta["numer_uchwaly"] = match_numer.group(1)

    # Data uchwalenia - TYLKO gdy występuje w kontekście "Data uchwalenia X" / "uchwalono X"
    # / "z dnia X". NIE bierzemy pierwszej daty ISO z tekstu, bo dla POG projektowanego
    # to jest "Obowiązuje od", a nie data uchwały.
    match_data_uchwaly = re.search(
        r"(?:data\s+uchwal[a-z]+|uchwalon[ao]|z\s+dnia)\s*[: ]\s*(\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{4})",
        tekst_czysty,
        flags=re.IGNORECASE
    )
    if match_data_uchwaly:
        meta["data_uchwaly"] = match_data_uchwaly.group(1)

    # Data obowiązywania od - POG/MPZP często podaje "Obowiązuje od 2025-02-14"
    match_data_obow = re.search(
        r"obowi[ąa]zuje\s+od\s*[: ]?\s*(\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{4})",
        tekst_czysty,
        flags=re.IGNORECASE
    )
    if match_data_obow:
        meta["data_obowiazywania_od"] = match_data_obow.group(1)

    # Status aktu - "w opracowaniu" (projekt) vs "obowiązujący"
    if "w opracowaniu" in tekst_lower:
        meta["status_aktu"] = "w opracowaniu"
    elif "obowi[ąa]zuj[ąa]cy" in tekst_lower or re.search(r"obowi[ąa]zuj[ąa]cy", tekst_lower):
        meta["status_aktu"] = "obowiązujący"
    elif meta["data_uchwaly"] or meta["numer_uchwaly"]:
        # Jeśli mamy numer/datę uchwały, ale status nie jest wypisany wprost -
        # domyślnie traktujemy jako obowiązujący (bo jest akt prawny).
        meta["status_aktu"] = "obowiązujący"

    # Nazwa dokumentu – fragment typu "Studium Uwarunkowań i Kierunków..."
    # lub "Miejscowy Plan Zagospodarowania..."
    match_nazwa = re.search(
        r"(Studium\s+Uwarunkowa[ńn][^.\n\t]{0,200}|Miejscow[a-ząż]+\s+Plan[^.\n\t]{0,200}|Plan\s+Og[óo]ln[a-ząż]+[^.\n\t]{0,200})",
        tekst_czysty,
        flags=re.IGNORECASE
    )
    if match_nazwa:
        nazwa = match_nazwa.group(1).strip()
        # Ucięcie przed znanymi markerami kolejnych pól w odpowiedzi WMS
        # (zapobiega połknięciu "Typ: MPZP Numer uchwały: ... Data uchwalenia: ...")
        markery_konca = [
            "Typ:", "Typ ", "Numer uchwały", "Numer uchwaly",
            "Data uchwalenia", "Data wejścia", "Data wejscia",
            "Uchwała:", "Uchwala:", "Link", "Rodzaj",
            "Status", "Pokaż legend", "Pokaz legend",
            "RysunekAktu", "Akt planowania", "Dziennik Urzędowy",
            "Dziennik Urzedowy",
        ]
        for marker in markery_konca:
            pos = nazwa.find(marker)
            if pos > 0:
                nazwa = nazwa[:pos]
        nazwa = re.sub(r"\s+", " ", nazwa).strip(" :-,")
        meta["nazwa_dokumentu"] = nazwa[:200]

    return meta

async def sprawdz_studium(lat: float, lon: float):
    bbox = _bbox_dla_punktu(lat, lon, bufor=1.0)

    params_studium = {
        "SERVICE": "WMS",
        "REQUEST": "GetFeatureInfo",
        "VERSION": "1.1.1",
        "LAYERS": "studium",
        "QUERY_LAYERS": "studium",
        "SRS": "EPSG:2180",
        "BBOX": bbox,
        "WIDTH": "10",
        "HEIGHT": "10",
        "X": "5",
        "Y": "5",
        "TRANSPARENT": "TRUE",
        "FORMAT": "image/png",
        "INFO_FORMAT": "text/html"
    }

    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            resp_studium = await client.get(KISKZP_WMS_URL, params=params_studium, timeout=15.0)
            tekst_studium = BeautifulSoup(resp_studium.text, "html.parser").get_text(separator=" ", strip=True).strip()

            if not _czy_to_sensowna_odpowiedz(tekst_studium):
                return None

            symbol, funkcja = _wyciagnij_symbol_i_funkcje_z_tekstu(tekst_studium)
            meta = _wyciagnij_metadane_uchwaly(tekst_studium)

            return {
                "typ_dokumentu": "studium",
                "zrodlo": "KISKZP",
                "status": "zolty",
                "czy_wymaga_wz": True,
                "symbol": symbol,
                "nazwa_strefy": funkcja,
                "numer_uchwaly": meta.get("numer_uchwaly"),
                "data_uchwaly": meta.get("data_uchwaly"),
                "data_obowiazywania_od": meta.get("data_obowiazywania_od"),
                "status_aktu": meta.get("status_aktu"),
                "nazwa_dokumentu": meta.get("nazwa_dokumentu"),
                "opis": "Nie znaleziono MPZP. Obszar objęty Studium. Do rozpoczęcia inwestycji wymagane będzie uzyskanie decyzji o Warunkach Zabudowy.",
                "surowy_tekst": _wyczysc_surowy_tekst(tekst_studium),
                "pewnosc": "srednia"
            }

        except Exception as e:
            return {
                "typ_dokumentu": "studium",
                "zrodlo": "KISKZP",
                "status": "zolty",
                "czy_wymaga_wz": None,
                "symbol": None,
                "nazwa_strefy": None,
                "opis": f"Błąd techniczny podczas sprawdzania Studium: {e}",
                "surowy_tekst": "",
                "pewnosc": "niska"
            }


async def sprawdz_planowanie_przestrzenne(lat: float, lon: float):
    """
    Odpytuje RÓWNOLEGLE wszystkie trzy źródła planistyczne i zwraca komplet danych.
    Deweloper widzi pełny obraz: czy jest MPZP + czy równolegle jest POG + czy równolegle jest Studium.
    Dodatkowo wyznacza 'glowne_zrodlo' na potrzeby werdyktu końcowego.
    """
    import asyncio

    wyniki = await asyncio.gather(
        sprawdz_mpzp_dokument(lat, lon),
        sprawdz_plan_ogolny(lat, lon),
        sprawdz_studium(lat, lon),
        return_exceptions=True
    )

    def _bezpieczny(w):
        return w if isinstance(w, dict) else None

    wynik_mpzp = _bezpieczny(wyniki[0])
    wynik_pog = _bezpieczny(wyniki[1])
    wynik_studium = _bezpieczny(wyniki[2])

    # Priorytet głównego źródła dla werdyktu: MPZP > POG > Studium
    glowne_zrodlo = "brak"
    glowny_wynik = None
    if wynik_mpzp and wynik_mpzp.get("pewnosc") != "niska":
        glowne_zrodlo = "mpzp"
        glowny_wynik = wynik_mpzp
    elif wynik_pog and wynik_pog.get("pewnosc") != "niska":
        glowne_zrodlo = "plan_ogolny"
        glowny_wynik = wynik_pog
    elif wynik_studium and wynik_studium.get("pewnosc") != "niska":
        glowne_zrodlo = "studium"
        glowny_wynik = wynik_studium

    if glowny_wynik is None:
        glowny_wynik = {
            "typ_dokumentu": "brak_danych",
            "zrodlo": "BRAK",
            "status": "zolty",
            "czy_wymaga_wz": True,
            "symbol": None,
            "nazwa_strefy": None,
            "numer_uchwaly": None,
            "data_uchwaly": None,
            "nazwa_dokumentu": None,
            "opis": "Nie znaleziono obowiązującego MPZP, Planu Ogólnego ani Studium w publicznych usługach WMS. Inwestycja może wymagać uzyskania decyzji o Warunkach Zabudowy.",
            "surowy_tekst": "",
            "pewnosc": "niska",
        }

    # Spłaszczenie: zwracamy pola głównego źródła (kompatybilność wstecz z main.py)
    # + pełny blok 'zrodla' z danymi ze wszystkich trzech systemów.
    return {
        **glowny_wynik,
        "glowne_zrodlo": glowne_zrodlo,
        "zrodla": {
            "mpzp": wynik_mpzp,
            "plan_ogolny": wynik_pog,
            "studium": wynik_studium,
        }
    }