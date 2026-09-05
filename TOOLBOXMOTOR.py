import base64
import os
import traceback
from flask import Flask, jsonify, request, send_from_directory

# Usamos la librería moderna oficial 'google-genai'
from google import genai
from google.genai import types

app = Flask(__name__, static_folder=".")

# ============================================================
# CONFIGURACIÓN GEMINI (PÓNLA SOLO AQUÍ 👇)
# ============================================================

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
print("🔑 ¿API KEY encontrada?:", GEMINI_API_KEY is not None)
print("🔑 Inicio de la clave:", GEMINI_API_KEY[:3] if GEMINI_API_KEY else "NO HAY CLAVE")
print("🔑 Longitud:", len(GEMINI_API_KEY) if GEMINI_API_KEY else 0)

# ============================================================

MODELO_GEMINI = "gemini-3.5-flash"

try:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    prueba = gemini_client.models.generate_content(
        model=MODELO_GEMINI,
        contents="Hola",
    )
    print(f"\n📡 Prueba de conexión Gemini: CONECTADO")
    print("✅ Cliente Gemini creado y validado correctamente.\n")
except Exception as error:
    gemini_client = None
    print(f"\n❌ ERROR CRÍTICO CONECTANDO CON GEMINI: {error}\n")


# ============================================================
# RUTAS DEL SERVIDOR
# ============================================================

@app.route("/")
def inicio():
    return send_from_directory(".", "index.html")

@app.route("/<path:filename>")
def archivos(filename):
    return send_from_directory(".", filename)

@app.route("/api/hola")
def hola():
    return jsonify({"ok": True, "mensaje": "TOOLBOX conectado 🚀"})

@app.route("/api/status")
def status():
    return jsonify({"ok": True, "gemini": gemini_client is not None, "servidor": "online"})


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
Ayuda especialmente con programación (Python, Flask, HTML, CSS, JS).
"""

@app.route("/api/ai", methods=["POST"])
@app.route("/api/chat", methods=["POST"])
def ai():
    if gemini_client is None:
        return jsonify({"ok": False, "error": "Gemini no configurado."}), 500

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

        prompt_final = f"{SYSTEM_PROMPT}\n\nHistorial de referencia de la conversación:{historial_texto}\n\nTOOLBOX AI:"

        # Llamada oficial rápida a Gemini
        respuesta = gemini_client.models.generate_content(
            model=MODELO_GEMINI,
            contents=prompt_final
        )

        texto_ia = respuesta.text if respuesta.text else "No pude procesar la respuesta."

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
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

