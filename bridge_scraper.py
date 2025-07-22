from scraper_tecnaria import scrape_tecnaria_results

# 🔍 Elenco delle sole query chiave, fondamentali
query_list = [
    "connettori",
    "chiodatrice P560",
    "sede Tecnaria",
    "contatti Tecnaria",
    "applicazioni",
    "FAQ"
]

contenuti = []

# 🔄 Ciclo su tutte le query
for query in query_list:
    print(f"🔎 Cerco: {query}...")
    risultato = scrape_tecnaria_results(query)
    if risultato:
        # 🧠 Etichetta coerente e utile per il bot
        blocco = f"📌 {query.upper()}\n{risultato.strip()}\n"
        contenuti.append(blocco)
    else:
        print(f"⚠️ Nessun risultato trovato per: {query}")

# 📝 Scrive tutto nel file usato dal bot
with open("documenti.txt", "w", encoding="utf-8") as f:
    f.write("\n\n".join(contenuti))

print("✅ File documenti.txt aggiornato con contenuti reali e coerenti.")
