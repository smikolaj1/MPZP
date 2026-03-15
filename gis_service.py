import httpx
from bs4 import BeautifulSoup
from pyproj import Transformer
import geopandas as gpd
import io
import re
import warnings

# Bezpieczne wyciszenie ostrzeżeń BeautifulSoup o parsowaniu XML jako HTML
warnings.filterwarnings("ignore", module="bs4")

ULDK_API_URL = "https://uldk.gugik.gov.pl/"
KIEG_WMS_URL = "https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaEwidencjiGruntow"
KIMPZP_WMS_URL = "https://mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaMiejscowychPlanowZagospodarowaniaPrzestrzennego"
KISKZP_WMS_URL = "https://mapy.geoportal.gov.pl/wss/ext/KrajowaIntegracjaStudiumKierunkowZagospodarowaniaPrzestrzennego"
# Prawidłowy adres odnaleziony w GetCapabilities:
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

            params_details = {"request": "GetParcelById", "id": wynik_id, "result": "voivodeship,county,commune,region,parcel"}
            resp_details = await client.get(ULDK_API_URL, params=params_details, timeout=10.0)
            
            linie_detale = resp_details.text.strip().split('\n')
            if len(linie_detale) > 1 and linie_detale[0] == "0":
                dane = linie_detale[1].replace("|", ",").split(",")
            else:
                dane = []

            return {
                "id_dzialki": wynik_id,
                "wojewodztwo": dane[0] if len(dane) > 0 else "Brak",
                "powiat": dane[1] if len(dane) > 1 else "Brak",
                "gmina": dane[2] if len(dane) > 2 else "Brak",
                "obreb": dane[3] if len(dane) > 3 else "Brak",
                "numer_dzialki": dane[4] if len(dane) > 4 else "Brak"
            }
        except Exception as e:
            return {"error": f"Błąd połączenia z serwerem ULDK: {e}"}


async def sprawdz_klase_gruntu(lat: float, lon: float):
    x_2180, y_2180 = transformer_do_2180.transform(lon, lat)
    bbox = f"{x_2180-5},{y_2180-5},{x_2180+5},{y_2180+5}"
    
    params = {
        "SERVICE": "WMS", "REQUEST": "GetFeatureInfo", "VERSION": "1.1.1",
        "LAYERS": "kontury,uzytki", "QUERY_LAYERS": "kontury,uzytki",
        "SRS": "EPSG:2180", "BBOX": bbox, "WIDTH": "10", "HEIGHT": "10",
        "X": "5", "Y": "5", "INFO_FORMAT": "text/xml" 
    }
    
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            resp = await client.get(KIEG_WMS_URL, params=params, timeout=15.0)
            soup = BeautifulSoup(resp.content, "xml")
            
            def pobierz_atrybut(nazwa):
                tag = soup.find("Attribute", {"Name": nazwa})
                return tag.text if tag else "Brak"

            grupa_rejestrowa = pobierz_atrybut("Grupa rejestrowa")
            oznaczenie_uzytku = pobierz_atrybut("Oznaczenie użytku")
            oznaczenie_konturu = pobierz_atrybut("Oznaczenie konturu")

            return {
                "surowy_wynik": "Dane XML (EPSG:2180)",
                "grupa_rejestrowa": grupa_rejestrowa,
                "klasa_gruntu": oznaczenie_konturu,
                "uzytek": f"Klasa: {oznaczenie_konturu} | Użytek: {oznaczenie_uzytku}"
            }
        except Exception:
            return {"grupa_rejestrowa": "Brak", "klasa_gruntu": "Brak", "uzytek": "Błąd serwera WMS"}


async def sprawdz_mpzp(lat: float, lon: float):
    x_2180, y_2180 = transformer_do_2180.transform(lon, lat)
    bbox = f"{x_2180-1},{y_2180-1},{x_2180+1},{y_2180+1}"
    
    warstwy = "wektor-pow,wektor-str,wektor-lzb,granice,raster,0,plan"
    
    params_bazowe = {
        "SERVICE": "WMS", "REQUEST": "GetFeatureInfo", "VERSION": "1.1.1",
        "LAYERS": warstwy, "QUERY_LAYERS": warstwy, "SRS": "EPSG:2180",
        "BBOX": bbox, "WIDTH": "3", "HEIGHT": "3", "X": "1", "Y": "1",
    }
    
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            # ----------------------------------------------------
            # 1. PRÓBA HTML + NISZCZYCIEL RAMEK (e-mapa)
            # ----------------------------------------------------
            params_html = params_bazowe.copy()
            params_html["INFO_FORMAT"] = "text/html"
            
            resp_html = await client.get(KIMPZP_WMS_URL, params=params_html, timeout=15.0)
            soup = BeautifulSoup(resp_html.text, "html.parser")
            
            iframes = soup.find_all('iframe')
            if iframes:
                print("\n🕵️ Wykryto urzędowe ramki (iframe)! Wchodzę do środka w masce Chrome...")
                teksty_z_ramek = []
                for iframe in iframes:
                    src = iframe.get('src')
                    if src:
                        if src.startswith('//'):
                            src = 'https:' + src
                        try:
                            naglowki = {
                                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                                "Referer": "https://mapy.geoportal.gov.pl/",
                                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
                            }
                            resp_iframe = await client.get(src, headers=naglowki, timeout=10.0)
                            print(f"🕵️ STATUS WEJŚCIA: {resp_iframe.status_code}")
                            
                            tekst_wewnetrzny = BeautifulSoup(resp_iframe.text, "html.parser").get_text(separator=" ", strip=True)
                            teksty_z_ramek.append(tekst_wewnetrzny)
                        except Exception as e:
                            print(f"Błąd wejścia do ramki: {e}")
                
                tekst_html = " ".join(teksty_z_ramek)
            else:
                tekst_html = soup.get_text(separator=" ", strip=True)

            tekst_lower = tekst_html.lower()
            tekst_do_sprawdzenia = tekst_html.replace("Geoserver GetFeatureInfo output", "").strip()
            
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
                "mswmsloadgetmapparams"
            ]
            
            if tekst_do_sprawdzenia and len(tekst_do_sprawdzenia) > 10 and not any(smiec in tekst_lower for smiec in czarna_lista):
                return tekst_html
                
            # ----------------------------------------------------
            # 2. PRÓBA GML (GetFeatureInfo udający wektory)
            # ----------------------------------------------------
            params_gml = params_bazowe.copy()
            params_gml["INFO_FORMAT"] = "application/vnd.ogc.gml"
            
            resp_gml = await client.get(KIMPZP_WMS_URL, params=params_gml, timeout=20.0)
            
            try:
                with io.BytesIO(resp_gml.content) as f:
                    gdf = gpd.read_file(f)
                if not gdf.empty:
                    znalezione = []
                    for col in gdf.columns:
                        if any(k in col.lower() for k in ['przeznaczenie', 'symbol', 'nazwa', 'funkcja', 'kod']):
                            wartosc = str(gdf.iloc[0][col])
                            if wartosc and wartosc.lower() != 'none':
                                znalezione.append(f"{col}: {wartosc}")
                    if znalezione:
                        return "Dane Wektorowe GML: " + " | ".join(znalezione)
            except Exception:
                pass

            soup_xml = BeautifulSoup(resp_gml.content, "xml")
            atrybuty = []
            for attr in soup_xml.find_all("Attribute"):
                nazwa = attr.get("Name", "").lower()
                if any(x in nazwa for x in ["przeznaczenie", "symbol", "funkcja", "opis"]):
                    atrybuty.append(f"{nazwa}: {attr.text}")
            if atrybuty:
                return "Dane GML: " + " | ".join(atrybuty)
            
            # ----------------------------------------------------
            # 3. PRÓBA AWARYJNA A: PLAN OGÓLNY GMINY (POG)
            # ----------------------------------------------------
            warstwy_pog = "aktPlanowaniaprzestrzennego,obszarZabSrodmiejskiej,obszarUzupelnieniaZabudowy,strefaPlanistyczna"
            
            params_pog = {
                "SERVICE": "WMS", "REQUEST": "GetFeatureInfo", "VERSION": "1.1.1",
                "LAYERS": warstwy_pog, "QUERY_LAYERS": warstwy_pog, "SRS": "EPSG:2180",
                "BBOX": bbox, "WIDTH": "10", "HEIGHT": "10", "X": "5", "Y": "5",
                "TRANSPARENT": "TRUE",
                "INFO_FORMAT": "text/html"
            }
            
            try:
                zapytanie_pog = client.build_request("GET", KIPOG_WMS_URL, params=params_pog)
                print(f"\n🕵️ ZAPYTANIE POG (Plan Ogólny) URL:\n{zapytanie_pog.url}")

                resp_pog = await client.get(KIPOG_WMS_URL, params=params_pog, timeout=10.0)
                
                print(f"🕵️ SUROWA ODPOWIEDŹ POG (Status: {resp_pog.status_code}, pierwsze 1000 znaków):")
                print(resp_pog.text[:1000])
                print("==================================================")

                tekst_pog = BeautifulSoup(resp_pog.text, "html.parser").get_text(separator=" ", strip=True)
                tekst_pog_czysty = tekst_pog.replace("Geoserver GetFeatureInfo output", "").strip()
                
                if tekst_pog_czysty and len(tekst_pog_czysty) > 10 and not any(smiec in tekst_pog.lower() for smiec in czarna_lista):
                    print(f"✅ [SZPIEG] Sukces! Złapałem Plan Ogólny: {tekst_pog[:200]}")
                    return f"DOKUMENT: PLAN OGÓLNY GMINY. {tekst_pog[:500]}"
            except Exception as e:
                print(f"⚠️ Błąd techniczny usługi Planu Ogólnego (POG): {e}")

            # ----------------------------------------------------
            # 4. PRÓBA AWARYJNA B: STUDIUM 
            # ----------------------------------------------------
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
            
            resp_studium = await client.get(KISKZP_WMS_URL, params=params_studium, timeout=15.0)
            tekst_studium = BeautifulSoup(resp_studium.text, "html.parser").get_text(separator=" ", strip=True)
            
            if tekst_studium and len(tekst_studium) > 15 and not any(smiec in tekst_studium.lower() for smiec in czarna_lista):
                return f"DOKUMENT: STUDIUM. {tekst_studium[:300]}"
            return "Brak danych MPZP oraz brak cyfrowych danych Studium. (Wymagane WZ)"
            
        except Exception as e:
            return f"ERROR: Błąd techniczny WMS/WFS (Timeout lub odrzucenie połączenia). Szczegóły: {e}"