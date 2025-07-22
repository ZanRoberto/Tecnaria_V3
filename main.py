<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Chatbot Tecnaria</title>
  <style>
    body {
      background-color: #f4f4f4;
      font-family: 'Segoe UI', sans-serif;
      color: #222;
      margin: 0;
      padding: 0;
    }
    .chat-container {
      max-width: 800px;
      margin: 50px auto;
      background-color: #ffffff;
      border: 1px solid #ddd;
      border-radius: 8px;
      box-shadow: 0 2px 10px rgba(0,0,0,0.1);
      overflow: hidden;
    }
    .chat-header {
      background-color: #003366;
      color: #fff;
      padding: 15px;
      font-size: 20px;
      text-align: center;
      font-weight: bold;
    }
    .chat-box {
      padding: 20px;
      height: 400px;
      overflow-y: auto;
      border-bottom: 1px solid #eee;
    }
    .chat-entry {
      margin-bottom: 20px;
    }
    .user {
      font-weight: bold;
      color: #003366;
    }
    .bot {
      margin-top: 5px;
    }
    .chat-form {
      display: flex;
      padding: 15px;
      background: #fafafa;
    }
    .chat-form input {
      flex: 1;
      padding: 10px;
      border: 1px solid #ccc;
      border-radius: 4px;
      font-size: 16px;
    }
    .chat-form button {
      background-color: #003366;
      color: #fff;
      border: none;
      padding: 10px 20px;
      margin-left: 10px;
      border-radius: 4px;
      font-size: 16px;
      cursor: pointer;
    }
    .chat-form button:hover {
      background-color: #00509e;
    }
    a {
      color: #00509e;
      text-decoration: underline;
    }
  </style>
</head>
<body>
  <div class="chat-container">
    <div class="chat-header">
      Chat Tecnica Tecnaria
    </div>
    <div class="chat-box" id="chat-box"></div>
    <form class="chat-form" id="chat-form">
      <input type="text" id="prompt" placeholder="Scrivi la tua domanda..." required />
      <button type="submit">Invia</button>
    </form>
  </div>

  <script>
    const chatBox = document.getElementById("chat-box");
    const chatForm = document.getElementById("chat-form");
    const promptInput = document.getElementById("prompt");

    chatForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const prompt = promptInput.value;
      appendMessage("Tu", prompt);
      promptInput.value = "";

      try {
        const response = await fetch("/ask", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompt })
        });
        const data = await response.json();
        appendMessage("Bot", data.answer || data.error);
      } catch (err) {
        appendMessage("Bot", "Errore nel contattare il server.");
      }
    });

    function appendMessage(sender, message) {
      const entry = document.createElement("div");
      entry.className = "chat-entry";
      const userText = `<div class="user">${sender}:</div>`;
      const botText = `<div class="bot">${linkify(message)}</div>`;
      entry.innerHTML = userText + botText;
      chatBox.appendChild(entry);
      chatBox.scrollTop = chatBox.scrollHeight;
    }

    function linkify(text) {
      const urlPattern = /((https?:\/\/)[^\s]+)/g;
      return text.replace(urlPattern, '<a href="$1" target="_blank">$1</a>');
    }
  </script>
</body>
</html>
