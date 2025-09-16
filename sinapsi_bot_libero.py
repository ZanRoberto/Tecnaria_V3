from openai import OpenAI

# 🔑 Inserisci la tua API Key
client = OpenAI(api_key="LA_TUA_API_KEY")

# Contesto fisso (system)
system_message = {
    "role": "system",
    "content": "Sei un esperto dei prodotti Tecnaria S.p.A. di Bassano del Grappa. Rispondi solo su questi prodotti (connettori, chiodatrici, accessori, capitolati, certificazioni, posa). Non parlare di prodotti non Tecnaria."
}

while True:
    domanda = input("\nFai una domanda su Tecnaria: ")
    if domanda.lower() in ["exit", "quit", "esci"]:
        break

    response = client.chat.completions.create(
        model="gpt-5",
        messages=[system_message, {"role": "user", "content": domanda}],
        temperature=0
    )

    print("\n👉 Risposta:")
    print(response.choices[0].message["content"])
