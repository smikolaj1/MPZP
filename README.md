# SprawdzDzialke.pl — API analizy uwarunkowań działki

API REST, które na podstawie współrzędnych geograficznych (lat/lon) zwraca kompleksowy raport o działce ewidencyjnej w Polsce. Łączy dane z 9 publicznych źródeł przestrzennych w jeden spójny werdykt inwestycyjny dla pośredników nieruchomości i deweloperów.

> **Status:** MVP 2.0 — B2B Ready. Projekt portfolio / micro-SaaS w fazie rozwoju.

---

## Co robi

Wpisujesz współrzędne, dostajesz raport JSON z:

- **Werdyktem końcowym** (zielony / żółty / czerwony) z opisem AI
- **Identyfikacją działki** (województwo, powiat, gmina, obręb, numer, powierzchnia w m²/ha, geometria WKT)
- **Planowaniem przestrzennym** — równolegle MPZP + Plan Ogólny + Studium z metadanymi uchwał
- **Klasą gruntu** z EGiB + analizą czy wymaga odrolnienia
- **Dostępem drogowym** na podstawie grupy rejestrowej
- **Ryzykami:** powódź, osuwiska, ochrona przyrody (Natura 2000, parki, rezerwaty, pomniki), nachylenie terenu z NMT
- **Infrastrukturą:** wykryte sieci uzbrojenia (woda, kanalizacja, gaz, prąd, telekomunikacja)
- **Otoczeniem:** POI w kategoriach (szkoły, sklepy, transport, zdrowie), obiekty uciążliwe z progami alarmowymi (oczyszczalnia, wysypisko, autostrada itd.), klasa najbliższej drogi
- **Listą next-steps** dla klienta

---

## Tech stack

- **Python 3.11+**, **FastAPI**, **Pydantic v2**, **httpx** (async)
- **Shapely** + **pyproj** (transformacje EPSG:4326 ↔ EPSG:2180, geometria działki, liczenie powierzchni)
- **GeoPandas** (parsowanie odpowiedzi GML z WMS)
- **BeautifulSoup4** (parsowanie HTML z WMS GetFeatureInfo)
- **OpenAI API** (`gpt-4o-mini`) — generowanie opisu klienckiego z guardem anti-halucynacja

---

## Architektura

```
        ┌─────────── FastAPI (main.py) ────────────┐
        │  POST /diagnoza/  →  asyncio.gather × 9  │
        └────────────────────┬─────────────────────┘
                             │
   ┌──────────┬──────────┬───┴────┬──────────┬──────────┐
   ▼          ▼          ▼        ▼          ▼          ▼
 ULDK       EGiB    Planowanie  ISOK       SOPO       OSM
(działka)  (grunt)  MPZP+POG+   (powódź)  (osuwiska) (POI,
                    Studium                            uciążliwe,
                    równolegle                         drogi)
                       │
                       ▼
                  AI (GPT-4o-mini)
                  opis kliencki
                  + ryzyko + podsumowanie
                       │
   ┌──────────┬────────┴────────┬──────────┐
   ▼          ▼                 ▼          ▼
 KIUT       GDOŚ            GUGiK NMT
(sieci)    (Natura 2000,   (nachylenie
            parki, pomniki) terenu)
```

Wszystkie 9 źródeł odpytywane są **równolegle przez `asyncio.gather`** — pojedynczy raport powstaje w 2-4 sekundy zamiast 15-25.

---

## Źródła danych (publiczne API rządowe)

| Źródło | Co dostajemy | Endpoint |
|---|---|---|
| ULDK (GUGiK) | ID działki, dane administracyjne, geometria, powierzchnia | `uldk.gugik.gov.pl` |
| KIEG WMS | Klasa gruntu, użytek, grupa rejestrowa | `integracja.gugik.gov.pl/.../KrajowaIntegracjaEwidencjiGruntow` |
| KIMPZP WMS | Miejscowy Plan Zagospodarowania Przestrzennego | `mapy.geoportal.gov.pl/wss/ext/...MiejscowychPlanow...` |
| KIPOG WMS | Plan Ogólny Gminy + parametry strefy (wysokość, intensywność, % zabudowy) | `mapy.geoportal.gov.pl/wss/ext/ProjektowanePlanyOgolneGmin` |
| KISKZP WMS | Studium uwarunkowań i kierunków zagospodarowania | `mapy.geoportal.gov.pl/wss/ext/...Studium...` |
| ISOK | Strefy ryzyka i zagrożenia powodziowego | `wody.isok.gov.pl` |
| SOPO (PIG) | Osuwiska, geozagrożenia | `cbdgmapa.pgi.gov.pl/.../sopo_obszary` |
| KIUT (GUGiK) | Sieci uzbrojenia: woda, kanalizacja, gaz, prąd, telekom | `integracja.gugik.gov.pl/.../KrajowaIntegracjaUzbrojeniaTerenu` |
| GDOŚ WMS | Formy ochrony przyrody (Natura 2000, parki, rezerwaty, pomniki) | `sdi.gdos.gov.pl/wms` |
| GUGiK NMT | Wysokość n.p.m., nachylenie terenu | `services.gugik.gov.pl/nmt` |
| OpenStreetMap | POI, obiekty uciążliwe, drogi | Overpass API (3 endpointy z fallbackiem) |

---

## Kluczowe decyzje projektowe

**1. Trzy źródła planistyczne sprawdzane jednocześnie**
MPZP, POG i Studium odpytywane są równolegle, a w odpowiedzi zwracane są **wszystkie trzy bloki** — `planowanie_przestrzenne.zrodla.{mpzp, plan_ogolny, studium}`. Pole `glowne_zrodlo` wskazuje hierarchię dla werdyktu (MPZP > POG > Studium). Klient widzi pełen obraz, nie tylko „jest plan/nie ma".

**2. Guard anti-halucynacja w warstwie AI**
Gdy publiczne WMS zwróci tylko metadane uchwały (numer, data, nazwa) bez symbolu strefy ani opisu funkcji — AI **nie wypowiada się** o możliwości zabudowy. Stwierdza fakt istnienia dokumentu i odsyła do rysunku planu w urzędzie. Krytyczne dla B2B: ryzyko prawne hallucynacji „można budować dom" jest nieakceptowalne.

**3. Konserwatywne progi nachylenia z NMT**
5 punktów próbkowania (centrum + N/S/E/W w 25 m), różnica wysokości dzielona przez 25 m (a nie 50 m między skrajnymi punktami). Lepiej zawyżyć szacunek nachylenia niż zaniżyć — u dewelopera niespodzianka kosztowa po zakupie to większy problem niż ostrożny raport przed zakupem.

**4. Rozróżnianie luki w danych vs błędu serwera**
W EGiB klasa gruntu może być pusta z dwóch różnych powodów: (a) starostwo nie wprowadziło klasyfikacji dla działki zabudowanej (typowe dla grupy B), (b) WMS GUGiK padł. Pole `status_danych: ok | luka_egib | blad` pozwala wygenerować adekwatny komunikat zamiast generycznego „brak".

**5. Graceful split parsowania ULDK**
Pierwsze zapytanie to metadane (nazwa pól rozdzielona `|`), drugie geometria (`geom_wkt`). Trzymane osobno, bo ULDK gubi się przy zapytaniu mieszanym. Powierzchnia liczona po stronie API z WKT przez Shapely (EPSG:2180 jest metryczny → `geom.area` daje m² bez reprojekcji).

**6. Czyszczenie odpowiedzi WMS**
GUGiK potrafi zwrócić odpowiedź z boilerplate'em ("O usłudze Usługa WMS pozwalająca…") doklejonym **po realnej treści**. Naiwna blacklista odfiltrowuje wartościowe odpowiedzi. Zamiast tego: wycinanie regexem stopki + `strip()` znanych prefiksów + dopiero potem walidacja czy odpowiedź jest sensowna.

---

## Uruchomienie

```bash
# 1. Klonuj
git clone <repo-url>
cd MPZP

# 2. Wirtualne środowisko
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac

# 3. Zależności
pip install -r requirements.txt

# 4. .env (klucz OpenAI dla warstwy AI)
echo OPENAI_API_KEY=sk-... > .env

# 5. Uruchom serwer
uvicorn main:app --reload
```

Serwer dostępny pod `http://localhost:8000`. Dokumentacja Swagger: `http://localhost:8000/docs`.

---

## Przykładowe użycie

```bash
curl -X POST http://localhost:8000/diagnoza/ \
  -H "Content-Type: application/json" \
  -d '{
    "lat": 51.789398,
    "lon": 18.039211,
    "cel_inwestycji": "dom jednorodzinny"
  }'
```

Odpowiedź (skrót):

```json
{
  "dane_podstawowe": {
    "id_dzialki": "300803_5.0005.227/3",
    "wojewodztwo": "wielkopolskie",
    "gmina": "Kępno",
    "powierzchnia_m2": 3491.5,
    "powierzchnia_ha": 0.3491,
    "geom_wkt": "POLYGON((...))"
  },
  "werdykt": {
    "status": "czerwony",
    "tytul": "Działka wymaga ostrożnej analizy przed wejściem w inwestycję",
    "opis": "..."
  },
  "planowanie_przestrzenne": {
    "glowne_zrodlo": "studium",
    "zrodla": { "mpzp": null, "plan_ogolny": null, "studium": {...} }
  },
  "ryzyka": {
    "powodz": {...},
    "osuwiska": {...},
    "ochrona_przyrody": {...},
    "nachylenie_terenu": {
      "status": "zielony",
      "nachylenie_proc": 0.4,
      "wysokosc_m": 178.3
    }
  },
  "next_steps": [...]
}
```

---

## Roadmapa

W rozwoju (po kolei): retry z exponential backoff, circuit breaker dla niestabilnych WMS-ów, rozróżnienie 503 (padł powiatowy geoportal) od 404 (punkt poza działką), rejestr zabytków NID, eksport PDF, mapa Leaflet, scoring numeryczny 1-100, batch z CSV.

---

## Autor

Projekt portfolio. Repozytorium pokazuje praktyczne podejście do integracji publicznych API rządowych w jeden produkt B2B z naciskiem na niezawodność i czytelność raportu dla użytkownika nietechnicznego.
