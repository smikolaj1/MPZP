import asyncio
import re
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from gis_service import pobierz_podstawy_dzialki, sprawdz_klase_gruntu, sprawdz_mpzp
from ai_service import analizuj_mpzp_ai
from premium.woda import sprawdz_zagrozenie_powodziowe
from premium.geologia import sprawdz_osuwiska
from premium.sieci import sprawdz_uzbrojenie_terenu

app = FastAPI(title="SprawdzDzialke.pl API", version="MVP 1.0 - Full Free")

class InputWspolrzedne(BaseModel):
    lat: float
    lon: float
    cel_inwestycji: str = "dom jednorodzinny"

class SwiatloDrogowe(BaseModel):
    parametr: str
    status: str
    opis: str

class RaportResponse(BaseModel):
    dane_podstawowe: dict
    swiatla_drogowe: list[SwiatloDrogowe]
    zacheta: str
    dupochron: str

def generuj_swiatla_drogowe(info_grunt, ocena_mpzp):
    # 1. Klasa Gruntu
    klasa = info_grunt.get("klasa_gruntu", "Brak")
    symbole = re.findall(r'[A-Za-z]+', klasa) if klasa != "Brak" else []
    
    status_gruntu = "zolty"
    if klasa == "Brak" or not klasa.strip():
        opis_gruntu = "Brak cyfrowych danych o klasie gruntu (EGiB) w publicznej usłudze WMS. Wymagana weryfikacja w rejestrach zamkniętych."
    elif "B" in symbole or "Ba" in symbole or "dr" in symbole:
        status_gruntu = "zielony"
        opis_gruntu = f"Grunt sklasyfikowany jako zurbanizowany/budowlany ({klasa}). Inwestycja nie wymaga procedury odrolnienia ani odlesienia."
    elif any(s.startswith("R") for s in symbole) or "Ls" in symbole or "Br" in symbole:
        status_gruntu = "czerwony"
        opis_gruntu = f"Grunt chroniony ({klasa}). Realizacja inwestycji wymaga przeprowadzenia formalnej procedury wyłączenia z produkcji rolniczej lub leśnej."
    else:
        opis_gruntu = f"Nietypowa klasyfikacja gruntu ({klasa}). Wymagana dodatkowa analiza geodezyjna."

    # 2. Grupa Rejestrowa
    grupa_rej = str(info_grunt.get("grupa_rejestrowa", "Brak")).strip()
    status_drogi = "zolty"
    
    if not grupa_rej or grupa_rej == "Brak":
        opis_drogi = "Dane o strukturze własności są zanonimizowane na publicznych serwerach (RODO). Wymagane pobranie pełnego wypisu z rejestru gruntów."
    elif grupa_rej in ["1", "2"]:
        status_drogi = "zielony"
        opis_drogi = "Teren należy do Skarbu Państwa lub JST (Gminy). Prawdopodobny bezpośredni dostęp do drogi publicznej."
    elif grupa_rej == "7":
        status_drogi = "zolty"
        opis_drogi = "Teren stanowi własność prywatną. Weryfikacja ewentualnych służebności przejazdu w Księdze Wieczystej wymaga pogłębionej analizy."
    elif grupa_rej == "9":
        status_drogi = "zolty"
        opis_drogi = "Teren należy do kościołów lub związków wyznaniowych. Konieczna weryfikacja statusu dojazdu."
    else:
        opis_drogi = f"Grupa rejestrowa: {grupa_rej.capitalize()}. Wymagana szczegółowa analiza prawna dostępu do drogi publicznej."
        
    return [
        {"parametr": "Przeznaczenie w MPZP", "status": ocena_mpzp.get("status", "zolty"), "opis": ocena_mpzp.get("opis", "Błąd analizy danych wektorowych.")},
        {"parametr": "Klasa Gruntu / Użytek", "status": status_gruntu, "opis": opis_gruntu},
        {"parametr": "Dostęp do drogi publicznej", "status": status_drogi, "opis": opis_drogi}
    ]

@app.post("/diagnoza/", response_model=RaportResponse)
async def pelna_diagnoza(dane: InputWspolrzedne):
    print(f"🚀 Uruchamiam pełny audyt dla: {dane.lat}, {dane.lon}")

    if not (49.0 <= dane.lat <= 54.8 and 14.1 <= dane.lon <= 24.15):
        raise HTTPException(status_code=400, detail="Nasze API obsługuje wyłącznie obszar Rzeczpospolitej Polskiej.")

    try:
        info_dzialki, info_grunt, surowy_tekst_mpzp, wynik_powodz, wynik_geologia, wynik_sieci = await asyncio.gather(
            pobierz_podstawy_dzialki(dane.lat, dane.lon),
            sprawdz_klase_gruntu(dane.lat, dane.lon),
            sprawdz_mpzp(dane.lat, dane.lon),
            sprawdz_zagrozenie_powodziowe(dane.lat, dane.lon),
            sprawdz_osuwiska(dane.lat, dane.lon),
            sprawdz_uzbrojenie_terenu(dane.lat, dane.lon)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd serwerów państwowych: {e}")

    if "error" in info_dzialki:
        raise HTTPException(status_code=404, detail=info_dzialki["error"])

    ocena_mpzp = await analizuj_mpzp_ai(surowy_tekst_mpzp, dane.cel_inwestycji)
    
    wszystkie_swiatla = generuj_swiatla_drogowe(info_grunt, ocena_mpzp)
    wszystkie_swiatla.append(wynik_powodz)
    wszystkie_swiatla.append(wynik_geologia)
    wszystkie_swiatla.append(wynik_sieci)
    
    return {
        "dane_podstawowe": info_dzialki,
        "swiatla_drogowe": wszystkie_swiatla,
        "zacheta": "Oto Twój pełny, darmowy raport. Pamiętaj, aby przed ostateczną decyzją zakupową skonsultować wyniki z lokalnym inżynierem.",
        "dupochron": "Uwaga prawna: Raport ma charakter informacyjny. Weryfikacja inżynieryjna oparta o dane z państwowych rejestrów WMS."
    }