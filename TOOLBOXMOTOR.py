import base64
import os
import traceback
import requests
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__, static_folder=".")

# ============================================================
# CONFIGURACIÓN GROQ (PÓNLA SOLO AQUÍ 👇)
# ============================================================

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
print("🔑 ¿API KEY encontrada?:", GROQ_API_KEY is not None)
print("🔑 Inicio de la clave:", GROQ_API_KEY[:3] if GROQ_API_KEY else "NO HAY CLAVE")
print("🔑 Longitud:", len(GROQ_API_KEY) if GROQ_API_KEY else 0)

# ============================================================

MODELO_GROQ = "openai/gpt-oss-120b"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

try:
    if not GROQ_API_KEY:
        raise ValueError("No hay GROQ_API_KEY configurada")

    respuesta_prueba = requests.post(
        GROQ_URL,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODELO_GROQ,
            "messages": [{"role": "user", "content": "Hola"}],
        },
        timeout=15,
    )
    respuesta_prueba.raise_for_status()
    groq_client_ok = True
    print(f"\n📡 Prueba de conexión Groq: CONECTADO")
    print("✅ Cliente Groq validado correctamente.\n")
except Exception as error:
    groq_client_ok = False
    print(f"\n❌ ERROR CRÍTICO CONECTANDO CON GROQ: {error}\n")


# ============================================================
# RUTAS DEL SERVIDOR
# ============================================================

@app.route("/")
def inicio():
    return send_from_directory(".", "index.html")
    
@app.route("/robots.txt")
def robots():
    texto_robots = "User-agent: *\nDisallow:\n\nSitemap: https://toolbox-zci6.onrender.com/sitemap.xml"
    return texto_robots, 200, {"Content-Type": "text/plain; charset=utf-8"}

@app.route("/<path:filename>")
def archivos(filename):
    return send_from_directory(".", filename)

@app.route("/api/hola")
def hola():
    return jsonify({"ok": True, "mensaje": "TOOLBOX conectado 🚀"})

@app.route("/api/status")
def status():
    return jsonify({"ok": True, "gemini": groq_client_ok, "servidor": "online"})


def construir_conversacion(messages):
    conversation_text = ""
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role", message.get("sender", "user"))
        content = message.get("content", message.get("text", ""))

        if not isinstance(content, str):
            content = str(content)
        if not content.strip():
            continue

        if role in ["assistant", "model", "toolbox", "bot"]:
            conversation_text += f"\n\nTOOLBOX AI:\n{content}"
        else:
            conversation_text += f"\n\nUSUARIO:\n{content}"
    return conversation_text


SYSTEM_PROMPT = """
Eres TOOLBOX AI. Inteligencia artificial central de la app TOOLBOX.
Responde normalmente en español, de forma amable, directa y práctica.
Ayuda a la gente a entender cosas y con lo que necestiten y sobre todo con la programacion si te lo piden (Python,,HTML, CSS, JS).
"""

@app.route("/api/ai", methods=["POST"])
@app.route("/api/chat", methods=["POST"])
def ai():
    if not groq_client_ok:
        return jsonify({"ok": False, "error": "Groq no configurado."}), 500

    try:
        data = request.get_json(silent=True) or {}
        
        # Extraemos los mensajes del JSON según los mapeos que tu frontend maneja
        messages = data.get("messages", data.get("history", []))

        # Si el JS mandó un objeto suelto de conversación en vez de la lista directa
        if isinstance(messages, dict) and "messages" in messages:
            messages = messages["messages"]

        if not isinstance(messages, list):
            messages = []
            
        ultimo_texto = data.get("message", data.get("text", data.get("texto", "")))
        if isinstance(ultimo_texto, dict):
            ultimo_texto = ultimo_texto.get("content", ultimo_texto.get("text", ""))

        # Extraemos el último mensaje escrito si hay lista
        if messages:
            messages = messages[-30:]
            ultimo_dict = messages[-1]
            if isinstance(ultimo_dict, dict) and not ultimo_texto:
                ultimo_texto = ultimo_dict.get("content", ultimo_dict.get("text", ""))
        
        if not ultimo_texto or str(ultimo_texto).strip() == "":
            ultimo_texto = "Hola"

        # Armamos el prompt con el contexto real e histórico
        historial_texto = construir_conversacion(messages)
        if not historial_texto.strip():
            historial_texto = f"\n\nUSUARIO:\n{ultimo_texto}"

        prompt_final = f"Historial de referencia de la conversación:{historial_texto}\n\nTOOLBOX AI:"

        # Llamada a Groq (formato OpenAI-compatible)
        respuesta_http = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODELO_GROQ,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt_final},
                ],
            },
            timeout=30,
        )
        respuesta_http.raise_for_status()
        respuesta_json = respuesta_http.json()

        texto_ia = (
            respuesta_json.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "No pude procesar la respuesta.")
        )

        # Inyectamos de vuelta la estructura exacta que tu JS necesita guardar en conversaciones
        messages.append({
            "role": "assistant", 
            "content": texto_ia, 
            "sender": "toolbox", 
            "text": texto_ia
        })

        # Mapeo universal absoluto de datos de respuesta
        return jsonify({
            "ok": True,
            "status": "success",
            "success": True,
            "resultado": texto_ia,
            "response": texto_ia,
            "text": texto_ia,
            "content": texto_ia,
            "message": texto_ia,
            "reply": texto_ia,
            "messages": messages,  # Tu JavaScript lee y guarda esto en su localStorage
            "choices": [{"message": {"content": texto_ia, "role": "assistant"}, "text": texto_ia}],
            "candidates": [{"content": {"parts": [{"text": texto_ia}], "role": "model"}}]
        })

    except Exception as error:
        print(traceback.format_exc())
        return jsonify({"ok": False, "error": str(error)}), 500

if __name__ == "__main__":
    # Render nos asignará un puerto automático, por eso usamos os.environ
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
