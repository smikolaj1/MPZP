import asyncio
import re
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from osm_service import sprawdz_otoczenie_osm
from premium.natura import sprawdz_ochrone_przyrody


from gis_service import (
    pobierz_podstawy_dzialki,
    sprawdz_klase_gruntu,
    sprawdz_planowanie_przestrzenne
)
from ai_service import generuj_opis_planistyczny_ai
from premium.woda import sprawdz_zagrozenie_powodziowe
from premium.geologia import sprawdz_osuwiska
from premium.sieci import sprawdz_uzbrojenie_terenu
from premium.nmt import sprawdz_nachylenie_terenu


app = FastAPI(title="SprawdzDzialke.pl API", version="MVP 2.0 - B2B Ready")


class InputWspolrzedne(BaseModel):
    lat: float
    lon: float
    cel_inwestycji: str = "dom jednorodzinny"


class RaportResponse(BaseModel):
    dane_podstawowe: dict
    werdykt: dict
    planowanie_przestrzenne: dict
    klasa_gruntu: dict
    dostep_drogowy: dict
    ryzyka: dict
    infrastruktura: dict
    otoczenie: dict
    next_steps: list[str]
    uwaga_prawna: str



def generuj_ocene_gruntu(info_grunt: dict) -> dict:
    klasa = info_grunt.get("klasa_gruntu", "Brak")
    uzytek = info_grunt.get("uzytek", "Brak")
    status_danych = info_grunt.get("status_danych", "ok")

    status = "zolty"
    opis = "Brak pełnych danych o klasie gruntu. Wymagana dodatkowa weryfikacja."

    # Przypadki specjalne sentinel-status PRZED regexem symboli — w innym wypadku
    # "Brak danych w EGiB" zostałoby potraktowane jako egzotyczny symbol.
    if status_danych == "luka_egib":
        opis = (
            "EGiB odpowiada, ale nie prowadzi dla tej działki klasyfikacji gruntu "
            "ani oznaczenia użytku. Typowo zdarza się to dla działek zabudowanych (grupa B) "
            "albo gdy starostwo nie wprowadziło jeszcze aktualizacji. Dane należy zweryfikować "
            "w wypisie z rejestru gruntów w starostwie."
        )
        return {"status": "zolty", "klasa": klasa, "uzytek": uzytek, "opis": opis}

    if status_danych == "blad":
        opis = "Serwer WMS EGiB nie zwrócił odpowiedzi. Wymagana weryfikacja w rejestrach urzędowych."
        return {"status": "zolty", "klasa": klasa, "uzytek": uzytek, "opis": opis}

    symbole = re.findall(r"[A-Za-z]+", klasa) if klasa and klasa != "Brak" else []

    if klasa == "Brak" or not str(klasa).strip():
        opis = "Brak cyfrowych danych o klasie gruntu w publicznej usłudze EGiB. Wymagana weryfikacja w rejestrach urzędowych."
    elif "B" in symbole or "Ba" in symbole or "dr" in symbole:
        status = "zielony"
        opis = f"Grunt sklasyfikowany jako zurbanizowany lub budowlany ({klasa}). Inwestycja co do zasady nie wymaga procedury odrolnienia ani odlesienia."
    elif any(s.startswith("R") for s in symbole) or "Ls" in symbole or "Br" in symbole:
        status = "czerwony"
        opis = f"Grunt ma charakter rolny lub leśny ({klasa}). Realizacja inwestycji może wymagać dodatkowych formalności związanych z wyłączeniem gruntu z produkcji."
    else:
        opis = f"Wykryto nietypową klasyfikację gruntu ({klasa}). Wskazana dodatkowa analiza geodezyjna lub prawna."

    return {
        "status": status,
        "klasa": klasa,
        "uzytek": uzytek,
        "opis": opis
    }


def generuj_ocene_dostepu_drogowego(info_grunt: dict) -> dict:
    grupa_rej = str(info_grunt.get("grupa_rejestrowa", "Brak")).strip()
    status = "zolty"

    if not grupa_rej or grupa_rej == "Brak":
        opis = "Dane o strukturze własności są niedostępne w publicznym widoku. Wymagana weryfikacja dostępu do drogi publicznej na podstawie dokumentów działki i księgi wieczystej."
    elif grupa_rej in ["1", "2"]:
        status = "zielony"
        opis = "Teren należy do Skarbu Państwa lub JST. Istnieje większe prawdopodobieństwo uporządkowanego dostępu do drogi publicznej, ale nadal warto to potwierdzić dokumentacyjnie."
    elif grupa_rej == "7":
        status = "zolty"
        opis = "Teren stanowi własność prywatną. Należy sprawdzić, czy działka ma formalny dostęp do drogi publicznej albo ustanowioną służebność przejazdu."
    elif grupa_rej == "9":
        status = "zolty"
        opis = "Teren należy do kościołów lub związków wyznaniowych. Konieczna jest dodatkowa weryfikacja statusu dojazdu do działki."
    else:
        opis = f"Wykryto grupę rejestrową {grupa_rej}. Sam wynik nie potwierdza dostępu do drogi publicznej, więc potrzebna jest dalsza analiza prawna."

    return {
        "status": status,
        "grupa_rejestrowa": grupa_rej,
        "opis": opis
    }


def generuj_next_steps(
    planowanie: dict,
    grunt: dict,
    dostep_drogowy: dict,
    powodz: dict,
    geologia: dict,
    sieci: dict,
    osm: dict,
    natura: dict,
    nmt: dict
) -> list[str]:
    kroki = []

    if planowanie.get("czy_wymaga_wz") is True:
        kroki.append("Sprawdzić możliwość uzyskania decyzji o Warunkach Zabudowy (WZ).")

    if dostep_drogowy.get("status") != "zielony":
        kroki.append("Potwierdzić formalny dostęp do drogi publicznej lub służebność przejazdu.")

    if grunt.get("status") != "zielony":
        kroki.append("Zweryfikować, czy grunt wymaga odrolnienia, odlesienia lub dodatkowych uzgodnień.")

    if powodz.get("status") == "czerwony":
        kroki.append("Wykonać szczegółową analizę ryzyka powodziowego przed zakupem lub rozpoczęciem inwestycji.")

    if geologia.get("status") == "czerwony":
        kroki.append("Zlecić badania geotechniczne i sprawdzić warunki posadowienia budynku.")

    # Nachylenie terenu z NMT — wpływa na fundamenty, odwodnienie i koszty ziemne.
    # Dla terenu stromego wymagane projekt konstrukcyjny + geotechnika.
    if nmt.get("status") == "czerwony":
        kroki.append(
            "Zamówić szczegółową niwelację działki i projekt posadowienia uwzględniający "
            "nachylenie terenu (tarasowanie, mury oporowe, fundamenty schodkowe)."
        )
    elif nmt.get("status") == "zolty" and nmt.get("nachylenie_proc") and nmt["nachylenie_proc"] >= 10:
        kroki.append(
            "Uwzględnić w kosztorysie inwestycji wzmocnione fundamenty i system odwodnienia "
            "działki — nachylenie terenu znacząco podnosi koszty prac ziemnych."
        )

    if natura.get("status") == "czerwony":
        kroki.append(
            "Sprawdzić plan ochrony i zarządzenia RDOŚ dla formy ochrony przyrody "
            "obejmującej działkę (park narodowy / rezerwat / Natura 2000)."
        )
    elif natura.get("status") == "zolty":
        formy = natura.get("formy_ochrony", [])
        ma_punktowy = any(f.get("rygor") == "punktowy" for f in formy)
        if ma_punktowy:
            kroki.append(
                "Zweryfikować na mapie zasadniczej dokładne położenie pomnika przyrody i jego "
                "strefy ochronnej (15 m). Jeśli strefa dotyka tylko brzegu działki, pozostała "
                "część może być w pełni zabudowalna."
            )
        else:
            kroki.append(
                "Zweryfikować zapisy uchwały powołującej obszar chronionego krajobrazu / "
                "parku krajobrazowego pod kątem ograniczeń zabudowy."
            )

    if sieci.get("status") != "zielony":
        kroki.append("Zweryfikować przebieg mediów i dostępność uzbrojenia na mapie zasadniczej lub u gestorów sieci.")
    else:
        kroki.append("Wystąpić o warunki techniczne przyłączenia mediów przed finalną decyzją inwestycyjną.")

    if osm.get("status") == "czerwony":
        alarmowe = [o for o in osm.get("obiekty_uciazliwe", []) if o.get("alarmowy")]
        if alarmowe:
            kroki.append(f"Sprawdzić wpływ obiektów uciążliwych w sąsiedztwie ({', '.join(a['typ'] for a in alarmowe[:2])}) na wartość inwestycji.")
    elif osm.get("droga") and osm["droga"].get("odleglosc_m", 0) > 150:
        kroki.append("Zweryfikować w OSM dojazd do działki - najbliższa droga jest oddalona.")
    
    if not kroki:
        kroki.append("Zweryfikować księgę wieczystą i dokumenty działki przed finalizacją transakcji.")

    return kroki


def generuj_werdykt_koncowy(
    planowanie: dict,
    grunt: dict,
    dostep_drogowy: dict,
    powodz: dict,
    geologia: dict,
    sieci: dict,
    natura: dict,
    nmt: dict,
    opis_ai: dict
) -> dict:
    statusy = [
        planowanie.get("status"),
        grunt.get("status"),
        dostep_drogowy.get("status"),
        powodz.get("status"),
        geologia.get("status"),
        sieci.get("status"),
        natura.get("status"),
        nmt.get("status")
    ]

    # Werdykt czyta szczegóły z głównego źródła (zrodla.mpzp / plan_ogolny / studium).
    # Po uproszczeniu kontrakt API nie trzyma już symbolu/tekstu na top-level planowania.
    glowne_zrodlo = planowanie.get("glowne_zrodlo") or "brak"
    zrodla = planowanie.get("zrodla") or {}
    glowny = zrodla.get(glowne_zrodlo) or {}

    # Dla flag restrykcyjnych zbieramy dane ze WSZYSTKICH źródeł - jeśli którekolwiek
    # mówi "las/zieleń/rolne", to jest sygnał ryzyka niezależnie od "głównego".
    symbol = str(glowny.get("symbol") or "").upper()
    nazwa_strefy = str(glowny.get("nazwa_strefy") or "").lower()
    wszystkie_teksty = " ".join(
        str((zrodla.get(k) or {}).get("surowy_tekst") or "").lower()
        + " " + str((zrodla.get(k) or {}).get("opis") or "").lower()
        + " " + str((zrodla.get(k) or {}).get("nazwa_strefy") or "").lower()
        for k in ("mpzp", "plan_ogolny", "studium")
    )

    plan_bez_danych_strefy = (
        bool(glowne_zrodlo) and glowne_zrodlo != "brak"
        and not glowny.get("symbol")
        and not glowny.get("nazwa_strefy")
    )

    restrykcyjne_flagi = any(x in symbol for x in ["ZP", "R", "LS", "SO"]) or any(
        fraza in wszystkie_teksty
        for fraza in [
            "tereny zieleni",
            "zieleni parkowej",
            "teren rolnictwa",
            "rolnictwa z zakazem zabudowy",
            "zakaz zabudowy",
            "teren lasu",
            "strefa otwarta",
            "teren zieleni naturalnej"
        ]
    )

    if restrykcyjne_flagi:
        status = "czerwony"
        tytul = "Działka ma istotne ograniczenia planistyczne"
    elif "czerwony" in statusy:
        status = "czerwony"
        tytul = "Działka wymaga ostrożnej analizy przed wejściem w inwestycję"
    elif plan_bez_danych_strefy:
        # Plan istnieje, ale publiczne WMS nie udostępniło symbolu/funkcji strefy.
        # Nie wiemy co wolno. Werdykt musi być ostrożny, nigdy "zielony".
        status = "zolty"
        tytul = "Plan istnieje, ale przeznaczenie działki wymaga weryfikacji w rysunku planu"
    elif statusy.count("zolty") >= 2:
        status = "zolty"
        tytul = "Działka wygląda obiecująco, ale wymaga dodatkowej weryfikacji"
    else:
        status = "zielony"
        tytul = "Działka wygląda korzystnie pod wstępny screening inwestycyjny"

    opis_ai_tekst = opis_ai.get("podsumowanie") or opis_ai.get("opis_kliencki")

    najwazniejsze = []
    if plan_bez_danych_strefy:
        najwazniejsze.append(
            "dla działki istnieje akt planistyczny, ale publiczne WMS nie udostępnia symbolu strefy - "
            "przeznaczenie trzeba sprawdzić w rysunku planu w urzędzie gminy"
        )
    elif planowanie.get("status") == "zielony" and not restrykcyjne_flagi:
        najwazniejsze.append("status planistyczny wygląda korzystnie")
    if restrykcyjne_flagi:
        najwazniejsze.append("planowanie przestrzenne wskazuje na istotne ograniczenia zabudowy")
    if powodz.get("status") == "zielony":
        najwazniejsze.append("brak sygnałów ryzyka powodziowego")
    if geologia.get("status") == "zielony":
        najwazniejsze.append("brak sygnałów ryzyka osuwiskowego")
    if natura.get("status") == "czerwony":
        najwazniejsze.append("działka w obszarze ścisłej ochrony przyrody (Natura 2000 / park / rezerwat)")
    elif natura.get("status") == "zolty":
        formy = natura.get("formy_ochrony", [])
        ma_punktowy = any(f.get("rygor") == "punktowy" for f in formy)
        if ma_punktowy:
            najwazniejsze.append("w pobliżu pomnik przyrody – strefa ochronna 15 m do sprawdzenia na mapie")
        else:
            najwazniejsze.append("działka w strefie ochrony przyrody z ograniczeniami")
    if sieci.get("status") == "zielony":
        najwazniejsze.append("w pobliżu wykryto uzbrojenie terenu")
    if dostep_drogowy.get("status") != "zielony":
        najwazniejsze.append("trzeba potwierdzić dostęp do drogi publicznej")
    if nmt.get("status") == "czerwony":
        najwazniejsze.append(
            f"teren bardzo stromy (ok. {nmt.get('nachylenie_proc')}%) — "
            f"zabudowa wymaga projektu konstrukcyjnego i tarasowania"
        )
    elif nmt.get("status") == "zolty" and nmt.get("nachylenie_proc") and nmt["nachylenie_proc"] >= 10:
        najwazniejsze.append(
            f"teren pochyły (ok. {nmt.get('nachylenie_proc')}%) — "
            f"wyższe koszty fundamentów i odwodnienia"
        )

    opis = opis_ai_tekst if opis_ai_tekst else "Wygenerowano wstępną ocenę działki na podstawie publicznych danych przestrzennych."

    if najwazniejsze:
        opis += " Najważniejsze wnioski: " + ", ".join(najwazniejsze) + "."

    return {
        "status": status,
        "tytul": tytul,
        "opis": opis
    }


@app.post("/diagnoza/", response_model=RaportResponse)
async def pelna_diagnoza(dane: InputWspolrzedne):
    print(f"🚀 Uruchamiam pełny audyt dla: {dane.lat}, {dane.lon}")

    if not (49.0 <= dane.lat <= 54.8 and 14.1 <= dane.lon <= 24.15):
        raise HTTPException(
            status_code=400,
            detail="Nasze API obsługuje wyłącznie obszar Polski."
        )

    try:
        (
            info_dzialki, info_grunt, wynik_planowania, wynik_powodz,
            wynik_geologia, wynik_sieci, wynik_osm, wynik_natura, wynik_nmt
        ) = await asyncio.gather(
            pobierz_podstawy_dzialki(dane.lat, dane.lon),
            sprawdz_klase_gruntu(dane.lat, dane.lon),
            sprawdz_planowanie_przestrzenne(dane.lat, dane.lon),
            sprawdz_zagrozenie_powodziowe(dane.lat, dane.lon),
            sprawdz_osuwiska(dane.lat, dane.lon),
            sprawdz_uzbrojenie_terenu(dane.lat, dane.lon),
            sprawdz_otoczenie_osm(dane.lat, dane.lon),
            sprawdz_ochrone_przyrody(dane.lat, dane.lon),
            sprawdz_nachylenie_terenu(dane.lat, dane.lon)
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd serwerów zewnętrznych: {e}")

    if "error" in info_dzialki:
        raise HTTPException(status_code=404, detail=info_dzialki["error"])

    opis_ai = await generuj_opis_planistyczny_ai(
        typ_dokumentu=wynik_planowania.get("typ_dokumentu", "brak_danych"),
        surowy_tekst_urzedowy=wynik_planowania.get("surowy_tekst", ""),
        cel_inwestycji=dane.cel_inwestycji,
        symbol=wynik_planowania.get("symbol"),
        nazwa_strefy=wynik_planowania.get("nazwa_strefy"),
    )

    # Top-level = SYNTEZA planowania (dla werdyktu i frontendu) — ma być SHORT.
    # Wszystkie szczegóły per źródło (symbol, nazwa uchwały, surowy tekst itd.)
    # siedzą WYŁĄCZNIE w planowanie_przestrzenne.zrodla.{mpzp,plan_ogolny,studium}.
    # Żadnej duplikacji.
    planowanie_przestrzenne = {
        "glowne_zrodlo": wynik_planowania.get("glowne_zrodlo"),
        "status": wynik_planowania.get("status"),
        "czy_wymaga_wz": wynik_planowania.get("czy_wymaga_wz"),
        "opis_kliencki": opis_ai.get("opis_kliencki"),
        "podsumowanie": opis_ai.get("podsumowanie"),
        "ryzyko_ai": opis_ai.get("ryzyko"),
        "zrodla": wynik_planowania.get("zrodla", {
            "mpzp": None,
            "plan_ogolny": None,
            "studium": None,
        }),
    }

    klasa_gruntu = generuj_ocene_gruntu(info_grunt)
    dostep_drogowy = generuj_ocene_dostepu_drogowego(info_grunt)

    ryzyka = {
        "powodz": wynik_powodz,
        "osuwiska": wynik_geologia,
        "ochrona_przyrody": wynik_natura,
        "nachylenie_terenu": wynik_nmt
    }

    infrastruktura = {
        "sieci": wynik_sieci
    }

    next_steps = generuj_next_steps(
        planowanie_przestrzenne,
        klasa_gruntu,
        dostep_drogowy,
        wynik_powodz,
        wynik_geologia,
        wynik_sieci,
        wynik_osm,
        wynik_natura,
        wynik_nmt
    )

    werdykt = generuj_werdykt_koncowy(
        planowanie_przestrzenne,
        klasa_gruntu,
        dostep_drogowy,
        wynik_powodz,
        wynik_geologia,
        wynik_sieci,
        wynik_natura,
        wynik_nmt,
        opis_ai
    )

    return {
        "dane_podstawowe": info_dzialki,
        "werdykt": werdykt,
        "planowanie_przestrzenne": planowanie_przestrzenne,
        "klasa_gruntu": klasa_gruntu,
        "dostep_drogowy": dostep_drogowy,
        "ryzyka": ryzyka,
        "infrastruktura": infrastruktura,
        "otoczenie": wynik_osm,
        "next_steps": next_steps,
        "uwaga_prawna": "Raport ma charakter informacyjny i stanowi wstępną ocenę działki na podstawie publicznych źródeł danych przestrzennych. Przed decyzją zakupową lub inwestycyjną zalecana jest weryfikacja prawna i techniczna."
    }
