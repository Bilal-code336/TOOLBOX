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
import uuid

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

# static_folder=None: si no, Flask crea una ruta /./<archivo> que se salta ruta_permitida()
# y permitiría descargar el código del servidor. Los archivos se sirven por la ruta /<path:filename>.
app = Flask(__name__, static_folder=None)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB max file size
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')

# Crear carpeta de uploads si no existe
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

ALLOWED_EXTENSIONS = {'txt', 'pdf', 'doc', 'docx', 'png', 'jpg', 'jpeg', 'gif', 'md'}

# ============================================================
# CONFIGURACIÓN (todo por variables de entorno en Render 👇)
# ============================================================
#
#   NVIDIA_API_KEY    → obligatoria para Nemotron 3 Ultra
#   TAVILY_API_KEY    → recomendada: activa la búsqueda en la web
#                       (plan gratuito en https://tavily.com)
#
# Opcionales:
#   MODELO_NVIDIA, NVIDIA_URL,
#   NVIDIA_MAX_TOKENS (4096 por defecto), NVIDIA_PENSAR (0 = respuesta rápida, 1 = razonar),
#   NVIDIA_TIMEOUT_LECTURA (segundos sin recibir datos, 60 por defecto),
#   BUSQUEDAS_POR_IP_HORA, BUSQUEDAS_MAX_DIA, BUSQUEDA_MAX_RONDAS
#
# IMPORTANTE (Render → Start Command). Con el timeout de 30 s que trae gunicorn por defecto,
# las respuestas largas se cortan. Usa:
#   gunicorn TOOLBOXMOTOR:app --worker-class gthread --threads 8 --timeout 180
# ============================================================

# --- NVIDIA Nemotron ---
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY")
print("[OK] NVIDIA_API_KEY encontrada:", bool(NVIDIA_API_KEY))

MODELO_NVIDIA = os.environ.get("MODELO_NVIDIA", "nvidia/nemotron-3-ultra-550b-a55b")
NVIDIA_URL = os.environ.get(
    "NVIDIA_URL", "https://integrate.api.nvidia.com/v1/chat/completions"
)
# Nemotron 3 Ultra es un modelo que "piensa" antes de contestar: con el razonamiento activado
# tarda decenas de segundos. Para un chat lo desactivamos (NVIDIA_PENSAR=1 lo vuelve a activar).
NVIDIA_PENSAR = os.environ.get("NVIDIA_PENSAR", "0").strip().lower() in ("1", "true", "si", "sí", "yes")
NVIDIA_MAX_TOKENS = int(os.environ.get("NVIDIA_MAX_TOKENS", "4096"))
NVIDIA_TIMEOUT_LECTURA = int(os.environ.get("NVIDIA_TIMEOUT_LECTURA", "60"))

# --- Búsqueda web ---
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")
TAVILY_URL = os.environ.get("TAVILY_URL", "https://api.tavily.com/search")
BUSQUEDA_RESULTADOS = int(os.environ.get("BUSQUEDA_RESULTADOS", "5"))
BUSQUEDA_MAX_RONDAS = int(os.environ.get("BUSQUEDA_MAX_RONDAS", "2"))
BUSQUEDAS_MAX_POR_RESPUESTA = int(os.environ.get("BUSQUEDAS_MAX_POR_RESPUESTA", "3"))
# Protege tu cuota gratuita de búsquedas frente a abusos
BUSQUEDAS_POR_IP_HORA = int(os.environ.get("BUSQUEDAS_POR_IP_HORA", "20"))
BUSQUEDAS_MAX_DIA = int(os.environ.get("BUSQUEDAS_MAX_DIA", "300"))

print(
    "[INFO] Búsqueda web:",
    "Tavily" if TAVILY_API_KEY else "sin TAVILY_API_KEY (se intentará DuckDuckGo si está instalado 'ddgs')",
)

# ============================================================


class ErrorNVIDIA(Exception):
    def __init__(self, status, detalle):
        super().__init__(detalle)
        self.status = status
        self.detalle = detalle


class BusquedaError(Exception):
    pass


def cabeceras_nvidia():
    return {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
    }


# Antes se hacía una llamada de prueba al modelo al arrancar (timeout 15 s). Con un modelo de este
# tamaño a veces tardaba más, la prueba fallaba y TODA la IA quedaba desactivada hasta reiniciar.
# Ahora solo se comprueba que exista la clave; si fuese inválida, el error sale en cada petición.
nvidia_client_ok = bool(NVIDIA_API_KEY)
if not nvidia_client_ok:
    print("\n[ERROR] Falta la variable de entorno NVIDIA_API_KEY\n")


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
    if partes[0].lower() in ("i18n", "uploads"):  # i18n: herramientas internas; uploads: archivos de usuarios
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
        "nvidia": nvidia_client_ok,
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
        return datetime.now(timezone.utc) + timedelta(hours=1)


def ip_cliente():
    return request.headers.get("X-Forwarded-For", request.remote_addr).split(",")[0].strip()


def limpiar_texto(texto):
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", texto or "")


ROLES_ASISTENTE = {"assistant", "toolbox", "ai", "bot", "model", "system"}


def construir_conversacion(messages):
    lineas = []
    for m in (messages or [])[-12:]:
        rol = m.get("role", m.get("sender", "user"))
        texto = m.get("content", m.get("text", ""))
        if not isinstance(texto, str):
            texto = str(texto)
        texto = texto.strip()
        if not texto:
            continue
        if rol in ROLES_ASISTENTE:
            lineas.append(f"TOOLBOX AI: {texto}")
        else:
            lineas.append(f"USUARIO: {texto}")
    return "\n".join(lineas)


def linea_idioma(codigo):
    if not codigo:
        return ""
    return {
        "es": "\nResponde en español.",
        "en": "\nRespond in English.",
        "fr": "\nRépondez en français.",
        "ar": "\nأجب بالعربية.",
    }.get(codigo, "")


def proveedor_busqueda():
    if TAVILY_API_KEY:
        return "tavily"
    try:
        import ddgs  # noqa: F401
        return "duckduckgo"
    except Exception:
        return None


# ============================================================
# BÚSQUEDA WEB
# ============================================================

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


def _rango_tavily(recency):
    """Convierte los días que pide el modelo a un time_range válido de Tavily (nunca lanza error)."""
    try:
        dias = int(str(recency).strip())
    except (TypeError, ValueError):
        return None
    if dias <= 0:
        return None
    if dias <= 1:
        return "day"
    if dias <= 7:
        return "week"
    if dias <= 31:
        return "month"
    return "year"


def _buscar_tavily(consulta, recency=None):
    cuerpo = {
        "query": consulta,
        "max_results": BUSQUEDA_RESULTADOS,
        "search_depth": "basic",
        "include_answer": False,
    }
    rango = _rango_tavily(recency)
    if rango:
        cuerpo["time_range"] = rango

    r = requests.post(
        TAVILY_URL,
        headers={"Authorization": f"Bearer {TAVILY_API_KEY}", "Content-Type": "application/json"},
        json=cuerpo,
        timeout=15,
    )
    if r.status_code >= 400:
        raise BusquedaError(f"Tavily {r.status_code}: {r.text[:200]}")
    datos = r.json()
    return [
        {
            "id": i + 1,
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "domain": urlparse(item.get("url", "")).netloc.replace("www.", ""),
            "snippet": item.get("content", "")[:300],
        }
        for i, item in enumerate(datos.get("results", []))
    ]


def _buscar_duckduckgo(consulta):
    try:
        from ddgs import DDGS
    except Exception:
        raise BusquedaError("DuckDuckGo no disponible (instala 'ddgs').")

    with DDGS() as ddgs:
        resultados = list(ddgs.text(consulta, max_results=BUSQUEDA_RESULTADOS))

    return [
        {
            "id": i + 1,
            "title": item.get("title", ""),
            "url": item.get("href", ""),
            "domain": urlparse(item.get("href", "")).netloc.replace("www.", ""),
            "snippet": item.get("body", "")[:300],
        }
        for i, item in enumerate(resultados)
    ]


def buscar_en_web(consulta, recency=None):
    if TAVILY_API_KEY:
        return _buscar_tavily(consulta, recency)
    try:
        return _buscar_duckduckgo(consulta)
    except Exception as e:
        raise BusquedaError(str(e))


# ============================================================
# LLAMADAS A NVIDIA
# ============================================================

HERRAMIENTA_WEB = {
    "type": "function",
    "function": {
        "name": "buscar_en_web",
        "description": "Busca información actualizada en la web. Úsala cuando necesites datos recientes, noticias, precios, documentación actual, etc.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Consulta de búsqueda (máx 200 caracteres)"},
                "recency": {"type": "string", "description": "Recencia en días: '1', '7', '30', '365'"}
            },
            "required": ["query"]
        }
    }
}


def cuerpo_nvidia(mensajes, tools=None, stream=False, extras=True):
    cuerpo = {
        "model": MODELO_NVIDIA,
        "messages": mensajes,
        "stream": stream,
        "max_tokens": NVIDIA_MAX_TOKENS,  # sin esto el servidor aplica un tope bajo y corta las respuestas
    }
    if extras:
        cuerpo["chat_template_kwargs"] = {"enable_thinking": NVIDIA_PENSAR}
        if not NVIDIA_PENSAR:
            cuerpo["force_nonempty_content"] = True
    if tools:
        cuerpo["tools"] = tools
        cuerpo["tool_choice"] = "auto"
    return cuerpo


def _enviar_nvidia(mensajes, tools, stream, timeout):
    """POST a NVIDIA. Si rechaza los campos extra (400/422), reintenta una vez sin ellos."""
    for extras in (True, False):
        try:
            r = requests.post(
                NVIDIA_URL,
                headers=cabeceras_nvidia(),
                json=cuerpo_nvidia(mensajes, tools, stream, extras),
                timeout=timeout,
                stream=stream,
            )
        except requests.Timeout:
            raise ErrorNVIDIA(504, "Nemotron ha tardado demasiado en responder. Inténtalo de nuevo.")
        except requests.RequestException as e:
            raise ErrorNVIDIA(502, f"No se pudo conectar con NVIDIA: {e}")
        if extras and r.status_code in (400, 422):
            r.close()
            continue
        return r


def _lanzar_si_error(r):
    if r.status_code < 400:
        return
    detalle = ""
    try:
        datos = r.json()
        err = datos.get("error") if isinstance(datos, dict) else None
        if isinstance(err, dict):
            detalle = err.get("message", "")
        elif isinstance(err, str):
            detalle = err
        elif isinstance(datos, dict):
            detalle = str(datos.get("detail", ""))
    except ValueError:
        detalle = r.text[:200]
    r.close()
    raise ErrorNVIDIA(r.status_code, detalle or f"Error {r.status_code}")


def llamar_nvidia(mensajes, tools=None, timeout=120):
    """Llama a Nemotron 3 Ultra en NVIDIA (sin streaming; para herramientas cortas)."""
    r = _enviar_nvidia(mensajes, tools, False, timeout)
    _lanzar_si_error(r)
    try:
        return r.json()["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise ErrorNVIDIA(502, "Nemotron devolvió una respuesta no válida.")


def llamar_nvidia_stream(mensajes, tools=None):
    """
    Llama a Nemotron en modo streaming. Va generando:
      ("delta", texto) · ("ping", None) · y al final ("final", {content, tool_calls?, finish_reason})
    Así el navegador recibe datos desde el primer token y la conexión no se queda muda.
    """
    r = _enviar_nvidia(mensajes, tools, True, (10, NVIDIA_TIMEOUT_LECTURA))
    _lanzar_si_error(r)

    contenido = []
    llamadas = {}
    finish = None
    ultimo_ping = 0.0
    try:
        for linea in r.iter_lines(chunk_size=None):
            if not linea:
                continue
            if isinstance(linea, bytes):
                linea = linea.decode("utf-8", "replace")
            if not linea.startswith("data:"):
                continue
            carga = linea[5:].strip()
            if carga == "[DONE]":
                break
            try:
                trozo = json.loads(carga)
            except ValueError:
                continue
            opciones = trozo.get("choices") or []
            if not opciones:
                continue
            ch = opciones[0]
            delta = ch.get("delta") or {}

            texto = delta.get("content")
            if texto:
                contenido.append(texto)
                yield ("delta", texto)
            else:
                ahora = time.time()
                if ahora - ultimo_ping > 10:  # el modelo está razonando: mantenemos viva la conexión
                    ultimo_ping = ahora
                    yield ("ping", None)

            for tc in delta.get("tool_calls") or []:
                i = tc.get("index", 0)
                dest = llamadas.setdefault(
                    i, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                )
                if tc.get("id"):
                    dest["id"] = tc["id"]
                f = tc.get("function") or {}
                if f.get("name"):
                    dest["function"]["name"] = f["name"]
                if f.get("arguments"):
                    dest["function"]["arguments"] += f["arguments"]

            if ch.get("finish_reason"):
                finish = ch["finish_reason"]
    except requests.Timeout:
        raise ErrorNVIDIA(504, "Nemotron ha tardado demasiado en responder. Inténtalo de nuevo.")
    except requests.RequestException as e:
        raise ErrorNVIDIA(502, f"Se interrumpió la conexión con NVIDIA: {e}")
    finally:
        r.close()

    msg = {"content": "".join(contenido), "finish_reason": finish}
    if llamadas:
        lista = [llamadas[i] for i in sorted(llamadas)]
        for n, ll in enumerate(lista):
            ll["id"] = ll["id"] or f"call_{n}"
        msg["tool_calls"] = lista
    yield ("final", msg)


def ronda_modelo(conversacion, tools):
    """Una vuelta al modelo reenviando al navegador lo que va escribiendo. Devuelve el mensaje final."""
    msg = None
    for tipo, valor in llamar_nvidia_stream(conversacion, tools):
        if tipo == "delta":
            yield {"type": "delta", "text": valor}
        elif tipo == "ping":
            yield {"type": "ping"}
        else:
            msg = valor
    return msg or {"content": ""}


def mensaje_error_amigable(e):
    if isinstance(e, ErrorNVIDIA):
        if e.status == 429:
            return "Nemotron está recibiendo muchas peticiones. Inténtalo de nuevo en unos segundos."
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
            msg = yield from ronda_modelo(conversacion, [HERRAMIENTA_WEB] if usar_herramienta else None)
        except ErrorNVIDIA as e:
            # Si falla justo al usar herramientas (p. ej. llamada mal formada),
            # reintentamos una vez sin búsqueda en lugar de dar error.
            if usar_herramienta and e.status == 400:
                web_disponible = False
                web_error = web_error or "No se pudo usar la búsqueda en la web en esta respuesta."
                conversacion[0]["content"] = construir_system_prompt(False, idioma)
                msg = yield from ronda_modelo(conversacion, None)
            else:
                raise

        llamadas = msg.get("tool_calls") or []

        if llamadas and usar_herramienta and (msg.get("content") or "").strip():
            yield {"type": "reset"}  # texto suelto antes de la herramienta: el navegador lo borra

        if not llamadas or not usar_herramienta:
            texto = limpiar_respuesta(msg.get("content")) or "No pude procesar la respuesta."
            if msg.get("finish_reason") == "length":
                texto += "\n\n> ⚠️ **Ojo:** la respuesta se ha cortado por el límite de longitud. Escribe «continúa» para que siga."
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
            if not isinstance(args, dict):
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
                    "content": f"Error buscando: {e}. Responde sin buscar.",
                })
                continue

            # Ids globales: cada fuente conserva el mismo número en todas las búsquedas de la respuesta
            # (antes las fuentes nuevas conservaban su id local 1..5 y se repetían entre búsquedas).
            todos = []
            for r in resultados:
                if not r.get("url"):
                    continue
                if r["url"] in url_a_fuente:
                    r["id"] = url_a_fuente[r["url"]]
                else:
                    r["id"] = len(fuentes) + 1
                    url_a_fuente[r["url"]] = r["id"]
                    fuentes.append(r)
                todos.append(r)

            busquedas.append({"query": consulta, "results": len(todos)})
            yield {"type": "results", "query": consulta, "sources": [fuente_publica(f) for f in todos]}

            # Se le pasan al modelo TODOS los resultados (también los ya vistos), con su id global.
            # Antes, si una búsqueda solo repetía fuentes, el modelo recibía «no se encontraron resultados».
            texto_resultados = formatear_resultados_para_modelo(consulta, todos)
            conversacion.append({
                "role": "tool", "tool_call_id": id_llamada,
                "content": texto_resultados,
            })


def construir_system_prompt(web_disponible, idioma=None):
    base = (
        "Eres TOOLBOX AI, un asistente útil, honesto y conciso. "
        "Respondes en el idioma del usuario (o español por defecto). "
        "Puedes usar Markdown cuando mejore la lectura: títulos, listas, **negrita**, "
        "==resaltado==, ++subrayado++, ~~tachado~~, tablas, listas de tareas, "
        "citas con avisos (> 💡 **Consejo:**, > ⚠️ **Ojo:**, > ℹ️ **Nota:**), "
        "bloques de código con lenguaje, y esquemas visuales (```esquema con tema central "
        "en la primera línea y ramas con sangría de 2 espacios). "
        "No uses más de un esquema por respuesta salvo que lo pidan. "
        "Si el usuario solo conversa o hace una pregunta corta, responde en texto natural sin formato pesado."
    )
    if web_disponible:
        base += (
            "\n\nTIENES ACCESO A BÚSQUEDA WEB: usa la herramienta 'buscar_en_web' cuando "
            "necesites información actual, noticias, precios, documentación reciente, etc. "
            "Cita cada dato con su número entre corchetes [1], [2]... "
            "No busques para conocimiento general o preguntas triviales."
        )
    base += linea_idioma(idioma)
    return base


def extraer_peticion(data):
    """Extrae (messages, ultimo_texto) del JSON entrante (compatibilidad varios formatos)."""
    messages = data.get("messages") or data.get("history") or []
    ultimo_texto = (
        data.get("prompt")
        or data.get("message")
        or data.get("text")
        or (messages[-1].get("content") if messages and isinstance(messages[-1], dict) else "")
        or ""
    )
    return messages, str(ultimo_texto)


def sse(evento):
    return f"data: {json.dumps(evento, ensure_ascii=False)}\n\n"


# ============================================================
# API DE IA — CON STREAMING (la usa TOOLBOX AI)
# ============================================================

@app.route("/api/ai/stream", methods=["POST"])
def ai_stream():

    if not nvidia_client_ok:
        return jsonify({"ok": False, "error": "NVIDIA Nemotron no configurado."}), 500

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

    if not nvidia_client_ok:
        return jsonify({
            "ok": False,
            "error": "NVIDIA Nemotron no configurado."
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
                "Historial de referencia de la conversación:\n"
                f"{historial_texto}\n\n"
                "TOOLBOX AI:"
            )

            msg = llamar_nvidia([
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
        if extension in ('txt', 'md'):
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                return f.read()
        elif extension == 'pdf':
            try:
                import fitz  # PyMuPDF
                doc = fitz.open(filepath)
                texto = ""
                for page in doc:
                    texto += page.get_text()
                doc.close()
                return texto
            except Exception:
                return ""
        elif extension in ('doc', 'docx'):
            try:
                import docx
                doc = docx.Document(filepath)
                return "\n".join([p.text for p in doc.paragraphs])
            except Exception:
                return ""
        else:
            return ""
    except Exception:
        return ""


# Prompts del sistema para herramientas de texto
SYSTEM_PROMPT = (
    "Eres TOOLBOX AI, un asistente experto en procesamiento de texto. "
    "Recibes un texto del usuario y debes transformarlo según la herramienta solicitada. "
    "Mantén siempre el significado original. No añadas información nueva. "
    "No saludes, no te despidas, no expliques qué hiciste. "
    "Devuelve ÚNICAMENTE el texto transformado."
)


# --- Corrector ---
PROMPT_CORRECTOR = (
    "Corrige el texto del usuario: ortografía, tildes, puntuación, mayúsculas/minúsculas, "
    "gramática, concordancia, tipografía y errores de escritura. "
    "Mantén exactamente el significado, párrafos y formato original. "
    "Devuelve solo el texto corregido."
)

# --- Simplificador ---
PROMPT_SIMPLIFICADOR = (
    "Simplifica el texto del usuario: explica lo mismo con palabras sencillas, "
    "frases más cortas, ejemplos cotidianos si ayudan. Mantén TODA la información importante. "
    "No inventes, no cambies el significado. Devuelve solo el texto simplificado."
)

# --- Humanizador ---
PROMPT_HUMANIZADOR_BASE = (
    "Reescribe el texto con total libertad de redacción. No hagas sustituciones palabra por palabra: "
    "construye una versión nueva, natural y coherente. "
    "Evita abusar de conectores formales (\"en primer lugar\", \"por consiguiente\", \"en conclusión\", etc.). "
    "Prefiere vocabulario natural: \"usar\" sobre \"utilizar\", \"hacer\" sobre \"realizar\", "
    "\"después\" sobre \"posteriormente\", \"porque\" sobre \"debido a que\". "
    "Puedes cambiar orden de ideas, fusionar/dividir frases, eliminar repeticiones. "
    "El resultado debe leer como texto independiente, no como traducción. "
    "Mantén significado, datos, nombres y idioma original."
)

ESTILOS_HUMANIZADOR = {
    "simple": "Usa un español sencillo, directo y natural. Prioriza palabras habituales, frases claras y lectura fácil. No rebajes precisión ni elimines información.",
    "pro": "Usa redacción profesional y, cuando corresponda, académica. Precisa, clara y seria, pero sin sonar rígida, excesivamente formal o mecánica.",
}


# --- Planificador ---
PROMPT_PLANIFICADOR = (
    "Planifica mi semana. No uses Pomodoros.\n\n"
    "TAREAS:\n{tasks}\n\n"
    "HORARIOS:\n{availability}\n\n"
    "REGLAS:\n"
    "- Respeta exactamente los horarios.\n"
    "- Prioriza fechas límite y exámenes cercanos.\n"
    "- Respeta las duraciones indicadas; si falta duración, estima una razonable.\n"
    "- Divide tareas largas si hace falta.\n"
    "- No llenes todo: deja tiempo libre.\n"
    "- No solapes bloques.\n"
    "- Si no cabe todo, programa primero lo prioritario y manda el resto a pending.\n"
    "- Usa bloques normales como 16:00-17:30.\n"
    "- Devuelve SOLO JSON válido, sin Markdown.\n\n"
    "FORMATO:\n"
    "{\"title\":\"Semana organizada\",\"summary\":\"breve\",\"days\":["
    "{\"day\":\"Lunes\",\"blocks\":[{\"task\":\"Matemáticas\",\"start\":\"16:00\",\"end\":\"17:30\",\"priority\":\"alta\",\"deadline\":\"viernes\",\"note\":\"\"}]},"
    "{\"day\":\"Martes\",\"blocks\":[]},{\"day\":\"Miércoles\",\"blocks\":[]},"
    "{\"day\":\"Jueves\",\"blocks\":[]},{\"day\":\"Viernes\",\"blocks\":[]},"
    "{\"day\":\"Sábado\",\"blocks\":[]},{\"day\":\"Domingo\",\"blocks\":[]}],"
    "\"insights\":[],\"pending\":[]}"
)


# ============================================================
# UPLOAD Y ANÁLISIS DE DOCUMENTOS
# ============================================================

@app.route('/api/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"ok": False, "error": "No se envió archivo"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"ok": False, "error": "Nombre de archivo vacío"}), 400

    if not allowed_file(file.filename):
        return jsonify({"ok": False, "error": "Tipo de archivo no permitido"}), 400

    # Nombre único en disco: dos subidas a la vez con el mismo nombre se pisaban, y un nombre como
    # «日本.txt» se quedaba en «txt» (sin extensión) y la subida fallaba con un error 500.
    extension = file.filename.rsplit('.', 1)[1].lower()
    filename = secure_filename(file.filename) or f"archivo.{extension}"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], f"{uuid.uuid4().hex}.{extension}")
    file.save(filepath)

    texto = extraer_texto_archivo(filepath)

    # Limpiar archivo tras extraer texto
    try:
        os.remove(filepath)
    except Exception:
        pass

    if not texto.strip():
        return jsonify({"ok": False, "error": "No se pudo extraer texto del archivo"}), 400

    return jsonify({
        "ok": True,
        "filename": filename,
        "text": texto[:50000],  # límite 50k chars
        "length": len(texto)
    })


@app.route('/api/analyze-document', methods=['POST'])
def analyze_document():
    data = request.get_json(silent=True) or {}
    texto = data.get("text", "")
    pregunta = data.get("question", "")

    if not texto.strip():
        return jsonify({"ok": False, "error": "Texto vacío"}), 400

    if not nvidia_client_ok:
        return jsonify({"ok": False, "error": "NVIDIA Nemotron no configurado."}), 500

    try:
        if pregunta.strip():
            prompt = f"Documento:\n{texto[:30000]}\n\nPregunta: {pregunta}\n\nResponde basándote solo en el documento."
        else:
            prompt = f"Resume este documento en 5-8 puntos clave, con ==lo esencial resaltado==:\n\n{texto[:30000]}"

        msg = llamar_nvidia([
            {"role": "system", "content": "Eres un analista de documentos. Responde basándote solo en el texto proporcionado."},
            {"role": "user", "content": prompt}
        ], timeout=60)

        respuesta = msg.get("content") or "No se pudo analizar."

        return jsonify({"ok": True, "result": respuesta})

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"ok": False, "error": mensaje_error_amigable(e)}), 500


# ============================================================
# GENERACIÓN DE IMÁGENES (placeholder)
# ============================================================

@app.route('/api/generate-image', methods=['POST'])
def generate_image():
    return jsonify({
        "ok": False,
        "error": "Generación de imágenes no disponible en esta versión."
    }), 501


# ============================================================
# RESUMEN DE CONVERSACIÓN
# ============================================================

@app.route('/api/summarize-conversation', methods=['POST'])
def summarize_conversation():
    data = request.get_json(silent=True) or {}
    messages = data.get("messages", [])

    if not messages:
        return jsonify({"ok": False, "error": "Sin mensajes"}), 400

    if not nvidia_client_ok:
        return jsonify({"ok": False, "error": "NVIDIA Nemotron no configurado."}), 500

    try:
        texto = construir_conversacion(messages)
        prompt = f"Resume esta conversación en 3-5 puntos clave con ==resaltado==:\n\n{texto}"

        msg = llamar_nvidia([
            {"role": "system", "content": "Eres un resumidor experto. Extrae lo esencial."},
            {"role": "user", "content": prompt}
        ], timeout=30)

        respuesta = msg.get("content") or "No se pudo resumir."

        return jsonify({"ok": True, "summary": respuesta})

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"ok": False, "error": mensaje_error_amigable(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))