import os
import json
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))


async def generuj_opis_planistyczny_ai(
    typ_dokumentu: str,
    surowy_tekst_urzedowy: str,
    cel_inwestycji: str = "dom jednorodzinny",
    symbol: str | None = None,
    nazwa_strefy: str | None = None,
):
    tekst = str(surowy_tekst_urzedowy or "").strip()

    if not tekst or len(tekst) < 20:
        return {
            "opis_kliencki": "Brakuje wystarczających danych urzędowych do wygenerowania opisu.",
            "podsumowanie": "Wymagana weryfikacja ręczna.",
            "ryzyko": "srednie"
        }

    # KRYTYCZNE dla B2B: jeśli publiczne WMS nie zwróciło symbolu strefy ani
    # nazwy funkcji, mamy tylko metadane aktu prawnego (nazwa/numer/data uchwały),
    # a NIE mamy żadnej informacji o przeznaczeniu działki. W tym trybie AI
    # NIE MA PRAWA wypowiadać się czy można budować - tylko stwierdzić fakt
    # istnienia dokumentu i odesłać do weryfikacji w rysunku planu.
    ma_dane_strefy = bool(symbol) or bool(nazwa_strefy)

    if not ma_dane_strefy:
        prompt_systemowy = f"""
Jesteś analitykiem nieruchomości. Dostajesz tekst z publicznego WMS, który zawiera
WYŁĄCZNIE metadane aktu planistycznego (nazwa dokumentu, numer uchwały, data) -
ale NIE zawiera symbolu strefy ani opisu przeznaczenia działki.

BEZWZGLĘDNE ZASADY:
1. ZABRONIONE jest pisać, że coś można lub nie można na działce zbudować.
2. ZABRONIONE jest zgadywać funkcję terenu (mieszkaniowa, usługowa, rolna itd.).
3. ZABRONIONE są sformułowania typu "teren umożliwia", "plan pozwala", "można planować".
4. ZABRONIONE jest nawiązywanie do celu inwestycji użytkownika.
5. Obowiązkowo napisz, że dla ustalenia przeznaczenia działki konieczne jest
   sprawdzenie rysunku planu w urzędzie gminy lub w BIP gminy.
6. Opis ma stwierdzić tylko fakt: działka leży na obszarze objętym dokumentem
   {typ_dokumentu.upper()} o podanej nazwie/numerze/dacie.

Zwróć TYLKO czysty JSON:
{{
  "opis_kliencki": "2-3 zdania: stwierdzasz istnienie dokumentu i odsyłasz do rysunku planu",
  "podsumowanie": "jedno zdanie: plan istnieje, przeznaczenie wymaga weryfikacji w rysunku",
  "ryzyko": "srednie"
}}
"""
        prompt_user = f"""
Typ dokumentu: {typ_dokumentu}
Symbol strefy: BRAK w publicznym WMS
Nazwa strefy: BRAK w publicznym WMS

Treść urzędowa (tylko metadane aktu):
{tekst}
"""
    else:
        prompt_systemowy = f"""
Jesteś analitykiem nieruchomości pomagającym pośrednikom i firmom z rynku nieruchomości.
Twoim zadaniem jest uprościć urzędowy opis planistyczny dla klienta biznesowego.

Dane wejściowe:
- typ dokumentu: {typ_dokumentu}
- cel inwestycji: {cel_inwestycji.upper()}
- symbol strefy: {symbol or "brak"}
- nazwa strefy: {nazwa_strefy or "brak"}

Zasady:
1. NIE zmieniaj typu dokumentu.
2. NIE zgaduj rzeczy, których nie ma w treści.
3. Pisz prostym, profesjonalnym językiem.
4. Unikaj prawniczego bełkotu.
5. Odpowiedź ma być krótka i konkretna.
6. Nie używaj zwrotów typu "na podstawie tekstu", "z dokumentu wynika", "dane wskazują".
7. Uwzględnij praktyczne znaczenie dla pośrednika, inwestora albo firmy sprawdzającej działkę przed zakupem.
8. Nie pisz, że konkretna inwestycja na pewno może powstać, jeśli dokument mówi ogólnie o funkcji terenu.
9. Jeśli w tekście pojawiają się określenia takie jak: zieleń, park, rolnictwo, las, zakaz zabudowy, strefa otwarta, to opis ma być wyraźnie ostrożny i wskazywać na możliwe ograniczenia inwestycyjne.
10. Gdy dokument wskazuje ograniczenia, nie używaj pozytywnego tonu typu "sprzyja inwestycjom mieszkaniowym".
11. Jeśli symbol lub nazwa strefy są nieznane ("brak"), NIE wypowiadaj się o możliwości zabudowy - odeślij do rysunku planu.

Zwróć TYLKO czysty JSON w formacie:
{{
  "opis_kliencki": "2-4 zdania prostym językiem",
  "podsumowanie": "jedno krótkie zdanie",
  "ryzyko": "niskie albo srednie albo wysokie"
}}
"""

        prompt_user = f"""
Typ dokumentu: {typ_dokumentu}
Cel inwestycji: {cel_inwestycji}
Symbol strefy: {symbol or "brak"}
Nazwa strefy: {nazwa_strefy or "brak"}

Treść urzędowa:
{tekst}
"""

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": prompt_systemowy},
                {"role": "user", "content": prompt_user}
            ],
            temperature=0.2
        )

        odpowiedz_tekst = response.choices[0].message.content.strip()

        if odpowiedz_tekst.startswith("```json"):
            odpowiedz_tekst = odpowiedz_tekst.replace("```json", "").replace("```", "").strip()

        return json.loads(odpowiedz_tekst)

    except Exception:
        return {
            "opis_kliencki": "Wykryto dokument planistyczny, ale jego treść wymaga dokładniejszej interpretacji ręcznej.",
            "podsumowanie": "Wymagana dodatkowa analiza ekspercka.",
            "ryzyko": "srednie"
        }
