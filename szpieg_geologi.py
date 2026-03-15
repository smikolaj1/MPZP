import httpx
from bs4 import BeautifulSoup
import asyncio

async def sprawdz_spis_tresci_geologii():
    url = "https://cbdgmapa.pgi.gov.pl/arcgis/services/geozagrozenia/sopo_obszary/MapServer/WMSServer"
    params = {
        "SERVICE": "WMS", 
        "REQUEST": "GetCapabilities"
    }
    
    print("🏔️ Włamuję się do spisu treści Państwowego Instytutu Geologicznego (SOPO)...")
    
    # Wyłączamy weryfikację SSL (verify=False), bo państwowe certyfikaty czasem lubią wygasnąć
    async with httpx.AsyncClient(verify=False) as client:
        try:
            resp = await client.get(url, params=params, timeout=15.0)
            soup = BeautifulSoup(resp.content, "xml")
            
            warstwy = soup.find_all("Layer")
            print("\n--- ZNALEZIONE WARSTWY GEOLOGICZNE (SOPO) ---")
            for w in warstwy:
                nazwa = w.find("Name")
                tytul = w.find("Title")
                # Filtrujemy, żeby pokazać tylko te warstwy, które mają nazwę i tytuł
                if nazwa and tytul and nazwa.text:
                    print(f"ID Warstwy: {nazwa.text} | Opis: {tytul.text}")
            print("---------------------------------------------\n")
        except Exception as e:
            print(f"Błąd zwiadu: {e}")

if __name__ == "__main__":
    asyncio.run(sprawdz_spis_tresci_geologii())