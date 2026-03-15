import os
import json
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

async def analizuj_mpzp_ai(surowy_tekst_urzedowy: str, cel_inwestycji: str = "dom jednorodzinny"):
    tekst_str = str(surowy_tekst_urzedowy).strip()
    tekst_lower = tekst_str.lower()
    
    print(f"\n--- SUROWY TEKST MPZP DLA AI (Cel: {cel_inwestycji.upper()}) ---")
    print(tekst_str[:500])
    print("----------------------------------\n")

    if not tekst_str or len(tekst_str) < 15 or any(x in tekst_lower for x in ["no features", "error", "exception", "błąd", "brak danych"]):
        print("Odrzucono zapytanie - brak twardych danych z urzędu (lub błąd serwera). Blokuję halucynacje.")
        return {
            "status": "zolty",
            "opis": "Serwery urzędowe nie odpowiedziały w terminie lub gmina nie udostępnia cyfrowego planu (MPZP) w bazie WMS. Wymagana weryfikacja ręczna lub wystąpienie o WZ."
        }
    
    if not surowy_tekst_urzedowy or "Brak" in surowy_tekst_urzedowy or len(surowy_tekst_urzedowy.strip()) < 30:
        return {
            "status": "zolty",
            "opis": "Brak cyfrowego Planu Miejscowego. Inwestycja będzie wymagała wystąpienia o Warunki Zabudowy (WZ)."
        }

    prompt_systemowy = f"""
Jesteś analitykiem przestrzennym. Klasyfikujesz tekst urzędowy dla klienta, którego cel to: {cel_inwestycji.upper()}.
Masz bezwzględny zakaz używania zwrotów typu "na podstawie tekstu", "dane wskazują". Pisz bezpośrednio.

ZASADA BEZWZGLĘDNA: Wybierz dokładnie JEDEN z 4 scenariuszy poniżej, bazując na słowach kluczowych w tekście.

SCENARIUSZ 1 (PRIORYTET): Tekst zawiera słowo "PLAN OGÓLNY GMINY"
- "status": Oceń strefę (np. mieszkaniowa/SJ = "zielony" lub "zolty", las/zieleń = "czerwony").
- "opis": Sformułuj zwięźle: "Dla działki brak MPZP, jednak objęta jest nowym Planem Ogólnym Gminy (strefa: [WSTAW STREFĘ Z TEKSTU]). Do budowy wymagana decyzja WZ."

SCENARIUSZ 2 (PRIORYTET): Tekst zawiera słowo "STUDIUM"
- "status": "zolty"
- "opis": Złóż zdanie: "Dla działki brak obowiązującego MPZP, jednak objęta jest Studium (Uchwała [WSTAW NUMER I DATĘ Z TEKSTU]). Do rozpoczęcia budowy wymagane uzyskanie decyzji o Warunkach Zabudowy (WZ)."

SCENARIUSZ 3: Tekst zawiera twarde ustalenia MPZP (symbole np. MN, R, U, przeznaczenie)
- "status": "zielony" (jeśli plan pozwala na inwestycję), "czerwony" (jeśli wyklucza) lub "zolty" (warunkowo).
- "opis": Podaj bezpośrednio przeznaczenie terenu w profesjonalnym żargonie urbanistycznym.

SCENARIUSZ 4 (FALLBACK): W tekście NIE MA słowa "STUDIUM", NIE MA słowa "PLAN OGÓLNY" i jest informacja o braku planu.
- "status": "zolty"
- "opis": "Brak cyfrowego Planu Miejscowego dla tego obszaru. Inwestycja będzie wymagała wystąpienia o Warunki Zabudowy (WZ)."

Zwróć TYLKO czysty obiekt JSON:
{{
  "status": "kolor",
  "opis": "Twój opis z wybranego scenariusza"
}}
"""

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": prompt_systemowy},
                {"role": "user", "content": f"Oto tekst z urzędu: {surowy_tekst_urzedowy}"}
            ],
            temperature=0.1
        )
        
        odpowiedz_tekst = response.choices[0].message.content.strip()
        if odpowiedz_tekst.startswith("```json"):
            odpowiedz_tekst = odpowiedz_tekst.replace("```json", "").replace("```", "").strip()
            
        return json.loads(odpowiedz_tekst)

    except Exception as e:
        return {
            "status": "zolty",
            "opis": "Wykryliśmy dokumenty planistyczne dla tej działki, ale ich format wymaga dokładnej analizy ręcznej przez naszego eksperta."
        }