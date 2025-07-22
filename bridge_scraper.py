from scraper_tecnaria import scrape_tecnaria_results

# 🔍 Elenco delle query da cercare su Tecnaria.com
query_list = [
    "sedi",
    "contatti",
    "prodotti",
    "orari",
    "francia",
    "certificazioni",
    "applicazioni",
    "assistenza",
    "FAQ"
]

contenuti = []

# 🔄 Ciclo su tutte le query
for query in query_list:
    print(f"🔎 Cerco: {query}...")
    risultato = scrape_tecnaria_results(query)
    if risultato:
        blocco = f"📌 {query.upper()}\n{risultato}\n"
        contenuti.append(blocco)
    else:
        print(f"⚠️ Nessun risultato trovato per: {query}")

# 📝 Scrive tutto nel file usato dal bot
with open("documenti.txt", "w", encoding="utf-8") as f:
    f.write("\n\n".join(contenuti))

print("✅ File documenti.txt aggiornato con tutte le query.")
