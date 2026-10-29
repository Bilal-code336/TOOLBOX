import json
import os
import re
import time
import traceback
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
import base64
import io

import requests
from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    request,
    send_from_directory,
    stream_with_context,
)
from werkzeug.utils import secure_filename

app = Flask(__name__, static_folder=".")
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB max file size
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')

# Crear carpeta de uploads si no existe
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

ALLOWED_EXTENSIONS = {'txt', 'pdf', 'doc', 'docx', 'png', 'jpg', 'jpeg', 'gif', 'md'}

# ============================================================
# CONFIGURACIÓN (todo por variables de entorno en Render 👇)
# ============================================================
#
#   GROQ_API_KEY      → obligatoria (la que ya tienes)
#   TAVILY_API_KEY    → recomendada: activa la búsqueda en la web
#                       (plan gratuito en https://tavily.com)
#
# Opcionales:
#   MODELO_GROQ, GROQ_REASONING_EFFORT (low | medium | high),
#   BUSQUEDAS_POR_IP_HORA, BUSQUEDAS_MAX_DIA, BUSQUEDA_MAX_RONDAS
# ============================================================

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
print("🔑 ¿API KEY encontrada?:", GROQ_API_KEY is not None)
print("🔑 Inicio de la clave:", GROQ_API_KEY[:3] if GROQ_API_KEY else "NO HAY CLAVE")
print("🔑 Longitud:", len(GROQ_API_KEY) if GROQ_API_KEY else 0)

MODELO_GROQ = os.environ.get("MODELO_GROQ", "openai/gpt-oss-120b")
GROQ_URL = os.environ.get(
    "GROQ_URL", "https://api.groq.com/openai/v1/chat/completions"
)
GROQ_REASONING_EFFORT = os.environ.get("GROQ_REASONING_EFFORT")  # opcional

# --- Búsqueda web ---
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")
TAVILY_URL = os.environ.get("TAVILY_URL", "https://api.tavily.com/search")
BUSQUEDA_RESULTADOS = int(os.environ.get("BUSQUEDA_RESULTADOS", "5"))
BUSQUEDA_MAX_RONDAS = int(os.environ.get("BUSQUEDA_MAX_RONDAS", "3"))
BUSQUEDAS_MAX_POR_RESPUESTA = int(os.environ.get("BUSQUEDAS_MAX_POR_RESPUESTA", "4"))
# Protege tu cuota gratuita de búsquedas frente a abusos
BUSQUEDAS_POR_IP_HORA = int(os.environ.get("BUSQUEDAS_POR_IP_HORA", "20"))
BUSQUEDAS_MAX_DIA = int(os.environ.get("BUSQUEDAS_MAX_DIA", "300"))

print(
    "🔎 Búsqueda web:",
    "Tavily" if TAVILY_API_KEY else "sin TAVILY_API_KEY (se intentará DuckDuckGo si está instalado 'ddgs')",
)

# ============================================================


class ErrorGroq(Exception):
    def __init__(self, status, detalle):
        super().__init__(detalle)
        self.status = status
        self.detalle = detalle


class BusquedaError(Exception):
    pass


def cabeceras_groq():
    return {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }


try:
    if not GROQ_API_KEY:
        raise ValueError("No hay GROQ_API_KEY configurada")

    respuesta_prueba = requests.post(
        GROQ_URL,
        headers=cabeceras_groq(),
        json={
            "model": MODELO_GROQ,
            "messages": [{"role": "user", "content": "Hola"}],
        },
        timeout=15,
    )
    respuesta_prueba.raise_for_status()
    groq_client_ok = True
    print("\n📡 Prueba de conexión Groq: CONECTADO")
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


# ============================================================
# IDIOMAS DE LA WEB (páginas generadas con build_i18n.py)
#   /      → español (index.html)
#   /en/   → inglés  (en/index.html)
#   /fr/   → francés (fr/index.html)
#   /ar/   → árabe (ar/index.html)
# Para añadir otro idioma: crea su carpeta y añade su código aquí.
# ============================================================

IDIOMAS_WEB = ("en", "fr", "ar")


def _pagina_idioma(codigo):
    return lambda: send_from_directory(codigo, "index.html")


def _redirigir(destino):
    return lambda: redirect(destino, code=301)


for _codigo in IDIOMAS_WEB:
    app.add_url_rule(f"/{_codigo}/", f"inicio_{_codigo}", _pagina_idioma(_codigo))
    app.add_url_rule(f"/{_codigo}", f"redirigir_{_codigo}", _redirigir(f"/{_codigo}/"))

app.add_url_rule("/es", "redirigir_es", _redirigir("/"))
app.add_url_rule("/es/", "redirigir_es_barra", _redirigir("/"))


# ============================================================
# TOOLBOX AI
# ============================================================

@app.route("/TOOLBOXAI")
def toolbox_ai():
    return send_from_directory(".", "TOOLBOXAI.html")


@app.route("/TOOLBOXAI.html")
def toolbox_ai_html():
    return send_from_directory(".", "TOOLBOXAI.html")


# ============================================================
# ROBOTS
# ============================================================

@app.route("/robots.txt")
def robots():
    texto_robots = (
        "User-agent: *\n"
        "Disallow:\n\n"
        "Sitemap: https://toolbox-zci6.onrender.com/sitemap.xml"
    )
    return texto_robots, 200, {
        "Content-Type": "text/plain; charset=utf-8"
    }


# ============================================================
# ARCHIVOS HTML, CSS, JS, IMÁGENES, ETC.
# ============================================================
# Se sirven archivos de la carpeta, pero NUNCA el código del servidor,
# archivos ocultos (.git, .env…) ni ficheros de configuración.

EXTENSIONES_BLOQUEADAS = {
    ".py", ".pyc", ".env", ".md", ".toml", ".cfg", ".ini", ".lock",
    ".sh", ".yml", ".yaml", ".sql", ".db", ".sqlite", ".log", ".orig",
}
NOMBRES_BLOQUEADOS = {
    "requirements.txt", "procfile", "render.yaml", "dockerfile",
    "runtime.txt", "package.json", "package-lock.json",
}


def ruta_permitida(filename):
    partes = [p for p in filename.replace("\\", "/").split("/") if p]
    if not partes:
        return False
    if any(p.startswith(".") or p == ".." for p in partes):
        return False
    if partes[0].lower() == "i18n":  # herramientas para generar idiomas: no se publican
        return False
    nombre = partes[-1].lower()
    if nombre in NOMBRES_BLOQUEADOS:
        return False
    return os.path.splitext(nombre)[1] not in EXTENSIONES_BLOQUEADAS


@app.route("/<path:filename>")
def archivos(filename):
    if not ruta_permitida(filename):
        abort(404)
    return send_from_directory(".", filename)


# ============================================================
# API HOLA
# ============================================================

@app.route("/api/hola")
def hola():
    return jsonify({
        "ok": True,
        "mensaje": "TOOLBOX conectado 🚀"
    })


# ============================================================
# API STATUS
# ============================================================

@app.route("/api/status")
def status():
    return jsonify({
        "ok": True,
        "gemini": groq_client_ok,
        "groq": groq_client_ok,
        "busqueda_web": proveedor_busqueda(),
        "servidor": "online"
    })


# ============================================================
# UTILIDADES
# ============================================================

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def hora_madrid():
    """Hora actual en España peninsular (con horario de verano)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Madrid"))
    except Exception:
        ahora = datetime.now(timezone.utc)
        # Aproximación si no hay base de zonas horarias: CET/CEST
        mes = ahora.month
        verano = 4 <= mes <= 9 or (mes == 3 and ahora.day >= 25) or (mes == 10 and ahora.day < 25)
        return ahora + timedelta(hours=2 if verano else 1)


def fecha_actual_texto():
    d = hora_madrid()
    return (
        f"{DIAS[d.weekday()]} {d.day} de {MESES[d.month - 1]} de {d.year}, "
        f"{d.strftime('%H:%M')} (hora de Madrid)"
    )


def dominio(url):
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def limpiar_texto(texto):
    return re.sub(r"\s+", " ", str(texto or "")).strip()


def ip_cliente():
    reenviada = request.headers.get("X-Forwarded-For", "")
    if reenviada:
        return reenviada.split(",")[0].strip()
    return request.remote_addr or "desconocida"


# ============================================================
# CONSTRUIR CONVERSACIÓN (modos simples: corrector, humanizador…)
# ============================================================

ROLES_ASISTENTE = ["assistant", "model", "toolbox", "bot"]


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

        if role in ROLES_ASISTENTE:
            conversation_text += f"\n\nTOOLBOX AI:\n{content}"
        else:
            conversation_text += f"\n\nUSUARIO:\n{content}"

    return conversation_text


# ============================================================
# PROMPTS
# ============================================================

# Prompt de siempre: se mantiene para las herramientas de texto
# (corrector, simplificador, humanizador…).
SYSTEM_PROMPT = """
Eres TOOLBOX AI. Inteligencia artificial central de la app TOOLBOX.
Responde normalmente en español, de forma amable, directa y práctica.
Ayuda a la gente a entender cosas y con lo que necesiten y sobre todo
con la programación si te lo piden (Python, HTML, CSS, JS).
"""

BLOQUE_WEB_ACTIVA = """
BÚSQUEDA EN LA WEB
Tienes la herramienta buscar_en_web. Úsala por tu cuenta cuando necesites información actual
o que pueda haber cambiado: noticias, precios, versiones, normativa, datos de empresas/personas,
resultados deportivos, horarios, o cuando el usuario pida buscar o comprobar algo reciente.
No la uses para conocimiento general estable, explicaciones, matemáticas, programación,
redacción creativa ni conversación normal.
- Haz consultas cortas y concretas (3-8 palabras). Puedes hacer varias búsquedas si hace falta.
- Basa tu respuesta en los resultados. Si no hay info fiable, dilo claro en lugar de inventar.
- Cita cada dato clave con su número de fuente entre corchetes [1] [2] justo después de la frase.
- NO menciones "según la fuente X", "en la web dice", "he buscado y...", "la página X comenta".
  Responde de forma natural como si supieras la info; la interfaz ya muestra las fuentes aparte.
- No añadas lista de fuentes al final.
"""

BLOQUE_WEB_INACTIVA = """
En esta conversación no tienes acceso a internet. Si el usuario pregunta por algo muy reciente
o que pueda haber cambiado, avísale de que tu información puede estar desactualizada.
"""


IDIOMAS_NOMBRE = {"es": "español", "en": "inglés", "fr": "francés"}


def linea_idioma(codigo):
    """Indica a la IA el idioma de la página desde la que escribe el usuario."""
    nombre = IDIOMAS_NOMBRE.get(str(codigo or "").lower()[:2])
    if not nombre:
        return ""
    return (
        f"\nIdioma de la interfaz del usuario: {nombre}. Responde en {nombre} "
        "salvo que el usuario escriba en otro idioma.\n"
    )


def construir_system_prompt(web_disponible, idioma=None):
    return (
        SYSTEM_PROMPT.strip()
        + linea_idioma(idioma)
        + f"\n\nFecha y hora actuales: {fecha_actual_texto()}.\n"
        + "Puedes usar Markdown cuando mejore la lectura (listas, tablas, código).\n"
        + (BLOQUE_WEB_ACTIVA if web_disponible else BLOQUE_WEB_INACTIVA)
    )


HERRAMIENTA_WEB = {
    "type": "function",
    "function": {
        "name": "buscar_en_web",
        "description": (
            "Busca información actual en internet y devuelve fuentes numeradas. "
            "Úsala para noticias, precios, versiones, resultados, normativa o datos "
            "que puedan haber cambiado, o cuando el usuario pida buscar o dar fuentes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Consulta corta y precisa (3 a 8 palabras). Incluye el año si es relevante.",
                },
                "recency": {
                    "type": "string",
                    "enum": ["day", "week", "month", "year"],
                    "description": "Opcional: limita los resultados a lo más reciente.",
                },
            },
            "required": ["query"],
        },
    },
}


# ============================================================
# BUSCADOR WEB
# ============================================================

def ddg_disponible():
    try:
        import ddgs  # noqa: F401
        return True
    except ImportError:
        try:
            import duckduckgo_search  # noqa: F401
            return True
        except ImportError:
            return False


def proveedor_busqueda():
    if TAVILY_API_KEY:
        return "tavily"
    if ddg_disponible():
        return "duckduckgo"
    return None


def _buscar_tavily(consulta, recency):
    cabeceras = {
        "Authorization": f"Bearer {TAVILY_API_KEY}",
        "Content-Type": "application/json",
    }
    cuerpo = {
        "query": consulta,
        "search_depth": "basic",
        "max_results": BUSQUEDA_RESULTADOS,
        "include_answer": False,
    }
    if recency in ("day", "week", "month", "year"):
        cuerpo["time_range"] = recency
        if recency in ("day", "week"):
            cuerpo["topic"] = "news"

    try:
        r = requests.post(TAVILY_URL, headers=cabeceras, json=cuerpo, timeout=20)
        if r.status_code == 401:  # algunas cuentas antiguas usan la clave en el cuerpo
            r = requests.post(
                TAVILY_URL,
                headers={"Content-Type": "application/json"},
                json={**cuerpo, "api_key": TAVILY_API_KEY},
                timeout=20,
            )
        if r.status_code in (400, 422):  # parámetros opcionales no admitidos
            basico = {"query": consulta, "max_results": BUSQUEDA_RESULTADOS}
            r = requests.post(TAVILY_URL, headers=cabeceras, json=basico, timeout=20)
    except requests.RequestException:
        raise BusquedaError("No se pudo conectar con el buscador.")

    if r.status_code in (401, 403):
        raise BusquedaError("El buscador no acepta la clave TAVILY_API_KEY.")
    if r.status_code in (429, 432, 433):
        raise BusquedaError("Se ha alcanzado el límite del buscador por ahora.")
    if r.status_code >= 400:
        raise BusquedaError(f"El buscador respondió con un error ({r.status_code}).")

    try:
        datos = r.json()
    except ValueError:
        raise BusquedaError("El buscador devolvió una respuesta no válida.")

    resultados = []
    for it in (datos.get("results") or [])[:BUSQUEDA_RESULTADOS]:
        url = it.get("url")
        if not url:
            continue
        resultados.append({
            "title": limpiar_texto(it.get("title")) or dominio(url),
            "url": url,
            "snippet": limpiar_texto(it.get("content"))[:700],
        })
    return resultados


def _buscar_duckduckgo(consulta):
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            raise BusquedaError(
                "No hay buscador configurado. Añade la variable TAVILY_API_KEY en Render."
            )
    try:
        items = list(DDGS().text(consulta, region="es-es", max_results=BUSQUEDA_RESULTADOS))
    except Exception:
        raise BusquedaError("No se pudo buscar con DuckDuckGo en este momento.")

    resultados = []
    for it in items:
        url = it.get("href") or it.get("url")
        if not url:
            continue
        resultados.append({
            "title": limpiar_texto(it.get("title")) or dominio(url),
            "url": url,
            "snippet": limpiar_texto(it.get("body"))[:700],
        })
    return resultados


def buscar_en_web(consulta, recency=None):
    proveedor = proveedor_busqueda()
    if proveedor == "tavily":
        return _buscar_tavily(consulta, recency)
    if proveedor == "duckduckgo":
        return _buscar_duckduckgo(consulta)
    raise BusquedaError(
        "No hay buscador configurado. Añade la variable TAVILY_API_KEY en Render."
    )


# --- Límites de uso para proteger tu cuota ---
_busquedas_ip = defaultdict(deque)
_busquedas_dia = {"fecha": "", "total": 0}


def permitir_busqueda(ip):
    ahora = time.time()
    hoy = time.strftime("%Y-%m-%d", time.gmtime())

    if _busquedas_dia["fecha"] != hoy:
        _busquedas_dia["fecha"] = hoy
        _busquedas_dia["total"] = 0

    if _busquedas_dia["total"] >= BUSQUEDAS_MAX_DIA:
        return False, "Se ha alcanzado el límite diario de búsquedas en la web."

    cola = _busquedas_ip[ip]
    while cola and ahora - cola[0] > 3600:
        cola.popleft()
    if len(cola) >= BUSQUEDAS_POR_IP_HORA:
        return False, "Has alcanzado el límite de búsquedas por hora. Inténtalo más tarde."

    cola.append(ahora)
    _busquedas_dia["total"] += 1
    return True, ""


# ============================================================
# LLAMADAS A GROQ
# ============================================================

def llamar_groq(mensajes, tools=None, timeout=60):
    cuerpo = {"model": MODELO_GROQ, "messages": mensajes}
    if tools:
        cuerpo["tools"] = tools
        cuerpo["tool_choice"] = "auto"
    if GROQ_REASONING_EFFORT:
        cuerpo["reasoning_effort"] = GROQ_REASONING_EFFORT

    try:
        r = requests.post(GROQ_URL, headers=cabeceras_groq(), json=cuerpo, timeout=timeout)
    except requests.Timeout:
        raise ErrorGroq(504, "TOOLBOX AI ha tardado demasiado en responder. Inténtalo de nuevo.")
    except requests.RequestException:
        raise ErrorGroq(502, "No se pudo conectar con el servicio de IA.")

    if r.status_code >= 400:
        detalle = ""
        try:
            detalle = (r.json().get("error") or {}).get("message", "")
        except ValueError:
            detalle = r.text[:200]
        raise ErrorGroq(r.status_code, detalle or f"Error {r.status_code}")

    try:
        return r.json()["choices"][0]["message"]
    except (ValueError, KeyError, IndexError):
        raise ErrorGroq(502, "El servicio de IA devolvió una respuesta no válida.")


def mensaje_error_amigable(e):
    if isinstance(e, ErrorGroq):
        if e.status == 429:
            return "TOOLBOX AI está recibiendo muchas peticiones. Inténtalo de nuevo en unos segundos."
        if e.status in (401, 403):
            return "El servicio de IA no ha aceptado la clave configurada."
        return e.detalle or "Error en el servicio de IA."
    return str(e)


def preparar_mensajes(messages, ultimo_texto=""):
    """Convierte el historial del cliente en mensajes limpios {role, content}."""
    limpios = []
    for m in (messages or [])[-30:]:
        if not isinstance(m, dict):
            continue
        rol = m.get("role", m.get("sender", "user"))
        contenido = m.get("content", m.get("text", ""))
        if not isinstance(contenido, str):
            contenido = str(contenido)
        contenido = contenido.strip()
        if not contenido:
            continue
        limpios.append({
            "role": "assistant" if rol in ROLES_ASISTENTE else "user",
            "content": contenido[:12000],
        })
    if not limpios:
        limpios.append({"role": "user", "content": (str(ultimo_texto).strip() or "Hola")[:12000]})
    return limpios


def limpiar_respuesta(texto):
    texto = re.sub(r"【[^】]*】", "", texto or "")  # marcas internas de algunos modelos
    return texto.strip()


# ============================================================
# CHAT CON BÚSQUEDA WEB AUTOMÁTICA
# ============================================================

def fuente_publica(f):
    return {
        "id": f["id"],
        "title": f["title"],
        "url": f["url"],
        "domain": f["domain"],
        "snippet": f["snippet"][:240],
    }


def formatear_resultados_para_modelo(consulta, nuevos):
    if not nuevos:
        return (
            f"No se encontraron resultados para «{consulta}». Dilo con claridad "
            "y responde con lo que sepas, avisando de que puede estar desactualizado."
        )
    partes = [f"Resultados de la búsqueda «{consulta}»:"]
    for f in nuevos:
        partes.append(
            f"\n[{f['id']}] {f['title']} ({f['domain']})\n"
            f"URL: {f['url']}\nExtracto: {f['snippet']}"
        )
    partes.append(
        "\nUsa estos datos y cita cada afirmación con su número entre corchetes, p. ej. [1]."
    )
    return "\n".join(partes)


def chat_eventos(mensajes_cliente, usar_web, ip, idioma=None):
    """
    Generador. Va emitiendo eventos y termina con uno de tipo 'done':
      start · search · results · search_error · limit · writing · done
    """
    mensajes = preparar_mensajes(mensajes_cliente)
    web_error = None

    web_disponible = bool(usar_web)
    if web_disponible and proveedor_busqueda() is None:
        # Sin buscador: el chat funciona igual, solo que sin internet.
        # (No molestamos al usuario; solo avisamos en los logs del servidor.)
        web_disponible = False
        print("⚠️ Búsqueda web desactivada: falta la variable TAVILY_API_KEY en Render.")

    conversacion = [
        {"role": "system", "content": construir_system_prompt(web_disponible, idioma)}
    ] + mensajes

    fuentes = []          # todas las fuentes de esta respuesta (id global)
    url_a_fuente = {}
    busquedas = []        # [{query, results}]
    total_busquedas = 0

    yield {"type": "start"}

    for ronda in range(BUSQUEDA_MAX_RONDAS + 1):
        usar_herramienta = web_disponible and ronda < BUSQUEDA_MAX_RONDAS

        try:
            msg = llamar_groq(conversacion, [HERRAMIENTA_WEB] if usar_herramienta else None)
        except ErrorGroq as e:
            # Si falla justo al usar herramientas (p. ej. llamada mal formada),
            # reintentamos una vez sin búsqueda en lugar de dar error.
            if usar_herramienta and e.status == 400:
                web_disponible = False
                web_error = web_error or "No se pudo usar la búsqueda en la web en esta respuesta."
                conversacion[0]["content"] = construir_system_prompt(False, idioma)
                msg = llamar_groq(conversacion, None)
            else:
                raise

        llamadas = msg.get("tool_calls") or []

        if not llamadas or not usar_herramienta:
            texto = limpiar_respuesta(msg.get("content")) or "No pude procesar la respuesta."
            yield {
                "type": "done",
                "reply": texto,
                "sources": [fuente_publica(f) for f in fuentes],
                "searches": busquedas,
                "searched": bool(busquedas),
                "web_error": web_error,
            }
            return

        # --- El modelo quiere buscar ---
        conversacion.append({
            "role": "assistant",
            "content": msg.get("content") or "",
            "tool_calls": llamadas,
        })

        for llamada in llamadas:
            funcion = llamada.get("function") or {}
            id_llamada = llamada.get("id") or ""

            if funcion.get("name") != "buscar_en_web":
                conversacion.append({
                    "role": "tool", "tool_call_id": id_llamada,
                    "content": "Herramienta no disponible.",
                })
                continue

            try:
                args = json.loads(funcion.get("arguments") or "{}")
            except ValueError:
                args = {}
            consulta = limpiar_texto(args.get("query"))[:200]
            recency = args.get("recency")

            if not consulta:
                conversacion.append({
                    "role": "tool", "tool_call_id": id_llamada,
                    "content": "Consulta vacía. Responde sin buscar.",
                })
                continue

            if not web_disponible:
                conversacion.append({
                    "role": "tool", "tool_call_id": id_llamada,
                    "content": "La búsqueda en la web no está disponible ahora. Responde sin buscar.",
                })
                continue

            if total_busquedas >= BUSQUEDAS_MAX_POR_RESPUESTA:
                conversacion.append({
                    "role": "tool", "tool_call_id": id_llamada,
                    "content": "Ya se hicieron suficientes búsquedas. Responde con lo que tienes.",
                })
                continue

            permitido, motivo = permitir_busqueda(ip)
            if not permitido:
                web_error = motivo
                web_disponible = False
                yield {"type": "limit", "reason": motivo}
                conversacion.append({
                    "role": "tool", "tool_call_id": id_llamada,
                    "content": f"{motivo} Responde sin buscar y avisa de que la información puede estar desactualizada.",
                })
                continue

            total_busquedas += 1
            yield {"type": "search", "query": consulta}

            try:
                resultados = buscar_en_web(consulta, recency)
            except BusquedaError as e:
                web_error = str(e)
                yield {"type": "search_error", "query": consulta, "error": str(e)}
                conversacion.append({
                    "role": "tool", "tool_call_id": id_llamada,
                    "content": f"Error al buscar: {e} Responde sin la búsqueda y avisa de que la información puede estar desactualizada.",
                })
                continue

            nuevos = []
            for r in resultados:
                existente = url_a_fuente.get(r["url"])
                if existente:
                    if existente not in nuevos:
                        nuevos.append(existente)
                    continue
                f = {
                    "id": len(fuentes) + 1,
                    "title": r["title"],
                    "url": r["url"],
                    "domain": dominio(r["url"]),
                    "snippet": r["snippet"],
                }
                fuentes.append(f)
                url_a_fuente[r["url"]] = f
                nuevos.append(f)

            busquedas.append({"query": consulta, "results": len(nuevos)})
            yield {
                "type": "results",
                "query": consulta,
                "sources": [fuente_publica(f) for f in nuevos],
            }
            conversacion.append({
                "role": "tool", "tool_call_id": id_llamada,
                "content": formatear_resultados_para_modelo(consulta, nuevos),
            })

        yield {"type": "writing"}

    # No debería llegar aquí (la última ronda no tiene herramientas)
    yield {
        "type": "done", "reply": "No pude completar la respuesta.",
        "sources": [fuente_publica(f) for f in fuentes], "searches": busquedas,
        "searched": bool(busquedas), "web_error": web_error,
    }


def extraer_peticion(data):
    """Saca mensajes y último texto del JSON, aceptando los formatos antiguos."""
    messages = data.get("messages", data.get("history", []))

    if isinstance(messages, dict) and "messages" in messages:
        messages = messages["messages"]
    if not isinstance(messages, list):
        messages = []

    ultimo_texto = data.get("message", data.get("text", data.get("texto", "")))
    if isinstance(ultimo_texto, dict):
        ultimo_texto = ultimo_texto.get("content", ultimo_texto.get("text", ""))

    if messages and not ultimo_texto:
        ultimo = messages[-1]
        if isinstance(ultimo, dict):
            ultimo_texto = ultimo.get("content", ultimo.get("text", ""))

    if not ultimo_texto or str(ultimo_texto).strip() == "":
        ultimo_texto = "Hola"

    return messages[-30:], ultimo_texto


def sse(evento):
    return f"data: {json.dumps(evento, ensure_ascii=False)}\n\n"


# ============================================================
# API DE IA — CON STREAMING (la usa TOOLBOX AI)
# ============================================================

@app.route("/api/ai/stream", methods=["POST"])
def ai_stream():

    if not groq_client_ok:
        return jsonify({"ok": False, "error": "Groq no configurado."}), 500

    data = request.get_json(silent=True) or {}
    messages, ultimo_texto = extraer_peticion(data)
    mensajes = preparar_mensajes(messages, ultimo_texto)
    usar_web = bool(data.get("web", True))
    ip = ip_cliente()
    idioma = data.get("lang")

    def generar():
        try:
            for evento in chat_eventos(mensajes, usar_web, ip, idioma):
                yield sse(evento)
        except Exception as error:
            print(traceback.format_exc())
            yield sse({"type": "error", "error": mensaje_error_amigable(error)})

    return Response(
        stream_with_context(generar()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================
# API DE INTELIGENCIA ARTIFICIAL (JSON de siempre)
# ============================================================

@app.route("/api/ai", methods=["POST"])
@app.route("/api/chat", methods=["POST"])
def ai():

    if not groq_client_ok:
        return jsonify({
            "ok": False,
            "error": "Groq no configurado."
        }), 500

    try:

        data = request.get_json(silent=True) or {}
        messages, ultimo_texto = extraer_peticion(data)
        modo = str(data.get("mode", "normal"))

        sources = []
        searches = []
        searched = False
        web_error = None

        if modo == "normal":
            # Chat normal: roles reales y, si lo pides con "web": true, búsqueda
            mensajes = preparar_mensajes(messages, ultimo_texto)
            final = None
            for evento in chat_eventos(mensajes, bool(data.get("web", False)), ip_cliente(), data.get("lang")):
                if evento["type"] == "done":
                    final = evento
            texto_ia = final["reply"]
            sources = final["sources"]
            searches = final["searches"]
            searched = final["searched"]
            web_error = final["web_error"]

        else:
            # Herramientas de texto (corrector, simplificador, humanizador…):
            # funcionan igual que siempre.
            historial_texto = construir_conversacion(messages)

            if not historial_texto.strip():
                historial_texto = f"\n\nUSUARIO:\n{ultimo_texto}"

            prompt_final = (
                "Historial de referencia de la conversación:"
                f"{historial_texto}\n\n"
                "TOOLBOX AI:"
            )

            msg = llamar_groq([
                {"role": "system", "content": SYSTEM_PROMPT + linea_idioma(data.get("lang"))},
                {"role": "user", "content": prompt_final},
            ], timeout=30)

            texto_ia = msg.get("content") or "No pude procesar la respuesta."

        messages = list(messages)
        messages.append({
            "role": "assistant",
            "content": texto_ia,
            "sender": "toolbox",
            "text": texto_ia
        })

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
            "sources": sources,
            "searches": searches,
            "searched": searched,
            "web_error": web_error,
            "messages": messages,
            "choices": [
                {
                    "message": {"content": texto_ia, "role": "assistant"},
                    "text": texto_ia
                }
            ],
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": texto_ia}],
                        "role": "model"
                    }
                }
            ]
        })

    except Exception as error:

        print(traceback.format_exc())

        return jsonify({
            "ok": False,
            "error": mensaje_error_amigable(error)
        }), 500


# ============================================================
# FUNCIONES DE IA MEJORADA
# ============================================================

def allowed_file(filename):
    """Verifica si la extensión del archivo está permitida"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extraer_texto_archivo(filepath):
    """Extrae texto de diferentes tipos de archivos"""
    extension = filepath.rsplit('.', 1)[1].lower()

    try:
        if extension == 'txt' or extension == 'md':
            with open(filepath, 'r', encoding='utf-8') as f:
                return f.read()

        elif extension == 'pdf':
            try:
                import PyPDF2
                texto = ""
                with open(filepath, 'rb') as f:
                    pdf_reader = PyPDF2.PdfReader(f)
                    for page in pdf_reader.pages:
                        texto += page.extract_text() + "\n"
                return texto
            except ImportError:
                return "[PDF detectado pero PyPDF2 no está instalado. Instala con: pip install PyPDF2]"

        elif extension in ['doc', 'docx']:
            try:
                import docx
                doc = docx.Document(filepath)
                texto = "\n".join([paragraph.text for paragraph in doc.paragraphs])
                return texto
            except ImportError:
                return "[Documento Word detectado pero python-docx no está instalado. Instala con: pip install python-docx]"

        elif extension in ['png', 'jpg', 'jpeg', 'gif']:
            # Para imágenes, retornamos la ruta para que se pueda procesar con visión
            with open(filepath, 'rb') as f:
                imagen_base64 = base64.b64encode(f.read()).decode('utf-8')
                return f"[IMAGEN: {extension.upper()}] (convertida a base64 para análisis con IA de visión)"

        else:
            return "[Formato de archivo no soportado]"

    except Exception as e:
        return f"[Error al procesar archivo: {str(e)}]"

def analizar_documento_largo(texto, max_chunk=4000):
    """
    Divide un documento largo en chunks y genera un análisis completo
    """
    # Dividir en chunks
    palabras = texto.split()
    chunks = []
    chunk_actual = []
    longitud_actual = 0

    for palabra in palabras:
        if longitud_actual + len(palabra) + 1 > max_chunk:
            chunks.append(" ".join(chunk_actual))
            chunk_actual = [palabra]
            longitud_actual = len(palabra)
        else:
            chunk_actual.append(palabra)
            longitud_actual += len(palabra) + 1

    if chunk_actual:
        chunks.append(" ".join(chunk_actual))

    return {
        "total_chunks": len(chunks),
        "chunks": chunks,
        "longitud_total": len(texto),
        "palabras_aproximadas": len(palabras)
    }

def generar_resumen_conversacion(messages):
    """
    Genera un resumen automático de una conversación
    """
    # Construir el texto de la conversación
    conversacion = []
    for msg in messages:
        if msg.get("role") == "user":
            conversacion.append(f"Usuario: {msg.get('content', '')}")
        elif msg.get("role") == "assistant":
            conversacion.append(f"TOOLBOX AI: {msg.get('content', '')}")

    texto_conversacion = "\n\n".join(conversacion)

    # Limitar si es muy largo
    if len(texto_conversacion) > 8000:
        texto_conversacion = texto_conversacion[:8000] + "...[conversación truncada]"

    prompt = f"""Eres un asistente experto en resumir conversaciones.

Analiza la siguiente conversación y genera un resumen claro y conciso que incluya:
1. Tema principal de la conversación
2. Puntos clave discutidos (3-5 puntos)
3. Conclusiones o resultados principales
4. Acciones pendientes (si las hay)

Conversación:
{texto_conversacion}

Responde con un resumen estructurado en formato Markdown."""

    try:
        respuesta = llamar_groq([
            {"role": "user", "content": prompt}
        ], timeout=30)

        return respuesta.get("content", "No se pudo generar el resumen")
    except Exception as e:
        return f"Error al generar resumen: {str(e)}"

# ============================================================
# ENDPOINTS DE IA MEJORADA
# ============================================================

@app.route('/api/upload', methods=['POST'])
def upload_file():
    """Endpoint para subir archivos"""
    if 'file' not in request.files:
        return jsonify({"error": "No se envió ningún archivo"}), 400

    file = request.files['file']

    if file.filename == '':
        return jsonify({"error": "No se seleccionó ningún archivo"}), 400

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        timestamp = int(time.time())
        filename = f"{timestamp}_{filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)

        file.save(filepath)

        # Extraer texto del archivo
        texto_extraido = extraer_texto_archivo(filepath)

        return jsonify({
            "ok": True,
            "filename": filename,
            "filepath": filepath,
            "texto": texto_extraido,
            "message": "Archivo subido y procesado correctamente"
        })

    return jsonify({"error": "Tipo de archivo no permitido"}), 400

@app.route('/api/analyze-document', methods=['POST'])
def analyze_document():
    """Endpoint para analizar documentos largos"""
    data = request.get_json(silent=True) or {}
    texto = data.get('texto', '')

    if not texto:
        return jsonify({"error": "No se proporcionó texto para analizar"}), 400

    # Analizar el documento
    analisis = analizar_documento_largo(texto)

    # Generar resúmenes de cada chunk
    resumenes = []
    for i, chunk in enumerate(analisis['chunks'][:5]):  # Limitar a 5 chunks
        try:
            prompt = f"Resume este fragmento de texto en 2-3 oraciones:\n\n{chunk}"
            respuesta = llamar_groq([{"role": "user", "content": prompt}], timeout=20)
            resumenes.append({
                "chunk": i + 1,
                "resumen": respuesta.get("content", "")
            })
        except Exception as e:
            resumenes.append({
                "chunk": i + 1,
                "resumen": f"Error: {str(e)}"
            })

    # Generar resumen general
    try:
        texto_para_resumen = texto[:6000] if len(texto) > 6000 else texto
        prompt_general = f"""Analiza este documento y proporciona:
1. Resumen ejecutivo (2-3 párrafos)
2. Temas principales (lista de 3-5 puntos)
3. Conclusiones clave

Documento:
{texto_para_resumen}"""

        respuesta = llamar_groq([{"role": "user", "content": prompt_general}], timeout=30)
        resumen_general = respuesta.get("content", "")
    except Exception as e:
        resumen_general = f"Error al generar resumen general: {str(e)}"

    return jsonify({
        "ok": True,
        "analisis": analisis,
        "resumenes_por_chunk": resumenes,
        "resumen_general": resumen_general
    })

@app.route('/api/generate-image', methods=['POST'])
def generate_image():
    """
    Endpoint para generar imágenes con IA
    Nota: Requiere integración con DALL-E, Stable Diffusion u otro servicio
    """
    data = request.get_json(silent=True) or {}
    prompt = data.get('prompt', '')

    if not prompt:
        return jsonify({"error": "No se proporcionó un prompt para generar la imagen"}), 400

    # Aquí integrarías con una API de generación de imágenes
    # Por ahora, retornamos un mensaje informativo
    return jsonify({
        "ok": False,
        "message": "La generación de imágenes requiere configurar una API externa como DALL-E, Stable Diffusion o similar.",
        "info": "Para activar esta función, necesitas:",
        "pasos": [
            "1. Obtener API key de OpenAI (DALL-E) o Stability AI",
            "2. Configurar la variable de entorno correspondiente",
            "3. Instalar las librerías necesarias"
        ],
        "prompt_recibido": prompt
    })

@app.route('/api/summarize-conversation', methods=['POST'])
def summarize_conversation():
    """Endpoint para generar resumen de conversaciones"""
    data = request.get_json(silent=True) or {}
    messages = data.get('messages', [])

    if not messages or len(messages) == 0:
        return jsonify({"error": "No se proporcionaron mensajes para resumir"}), 400

    try:
        resumen = generar_resumen_conversacion(messages)

        return jsonify({
            "ok": True,
            "resumen": resumen,
            "mensajes_analizados": len(messages)
        })
    except Exception as e:
        return jsonify({
            "error": f"Error al generar resumen: {str(e)}"
        }), 500

# ============================================================
# INICIAR SERVIDOR
# ============================================================

if __name__ == "__main__":

    # Render nos asignará un puerto automático
    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        threaded=True
    )