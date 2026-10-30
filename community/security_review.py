import json
import re
from typing import Dict, Any

try:
    from TOOLBOXMOTOR import llamar_nvidia, nvidia_client_ok
except ImportError:
    llamar_nvidia = None
    nvidia_client_ok = False

SECURITY_PROMPT = """
Eres un auditor de seguridad experto. Analiza el siguiente código de una herramienta web
que se ejecutará en un iframe sandboxado con CSP: connect-src 'none'; script-src 'self'.

Código de la herramienta:
Nombre: {name}
Descripción: {description}
Código HTML/JS:
{code}

Busca patrones peligrosos:
1. eval(), Function(), new Function(), setTimeout/setInterval con string
2. Importaciones dinámicas: import(), require(), System.import
3. Acceso a red: fetch(), XMLHttpRequest, WebSocket, navigator.sendBeacon
4. Acceso a almacenamiento: localStorage, sessionStorage, indexedDB, document.cookie
5. Manipulación de navegación: location.href, location.replace, window.open, top.location, parent.location
6. postMessage a parent/top/window.opener
7. Acceso a APIs sensibles: navigator.clipboard, navigator.geolocation, camera, microphone
8. WebRTC, WebAssembly con imports sospechosos
9. Prototype pollution, DOM clobbering
10. Cualquier intento de escapar del iframe sandbox

Responde SOLO con JSON válido:
{{
  "approved": true/false,
  "reason": "explicación breve en español",
  "risk_level": "low/medium/high/critical",
  "findings": ["hallazgo 1", "hallazgo 2"]
}}

Si approved=false, la herramienta NO se publicará.
"""

DANGEROUS_PATTERNS = [
    r'\beval\s*\(',
    r'\bFunction\s*\(',
    r'\bnew\s+Function\s*\(',
    r'\bsetTimeout\s*\(\s*["\']',
    r'\bsetInterval\s*\(\s*["\']',
    r'\bimport\s*\(',
    r'\brequire\s*\(',
    r'\bfetch\s*\(',
    r'\bXMLHttpRequest\b',
    r'\bWebSocket\b',
    r'\bnavigator\.sendBeacon\b',
    r'\blocalStorage\b',
    r'\bsessionStorage\b',
    r'\bindexedDB\b',
    r'\bdocument\.cookie\b',
    r'\blocation\.href\b',
    r'\blocation\.replace\b',
    r'\bwindow\.open\b',
    r'\btop\.location\b',
    r'\bparent\.location\b',
    r'\bpostMessage\s*\(',
    r'\bnavigator\.clipboard\b',
    r'\bnavigator\.geolocation\b',
]

class SecurityReview:
    def __init__(self):
        self.dangerous_regex = [re.compile(p, re.IGNORECASE) for p in DANGEROUS_PATTERNS]

    def _quick_scan(self, code: str) -> list:
        findings = []
        for pattern in self.dangerous_regex:
            if pattern.search(code):
                findings.append(f"Patrón peligroso detectado: {pattern.pattern}")
        return findings

    def review_tool(self, code: str, name: str, description: str) -> Dict[str, Any]:
        quick_findings = self._quick_scan(code)
        if quick_findings:
            return {
                "approved": False,
                "reason": "Código rechazado por patrones peligrosos detectados automáticamente.",
                "risk_level": "high",
                "findings": quick_findings[:5]
            }

        if not nvidia_client_ok or llamar_nvidia is None:
            return {
                "approved": True,
                "reason": "Revisión IA no disponible (modo desarrollo). Revisión manual recomendada.",
                "risk_level": "medium",
                "findings": ["IA no configurada - revisión automática omitida"]
            }

        prompt = SECURITY_PROMPT.format(
            name=name[:100],
            description=description[:500],
            code=code[:15000]
        )

        try:
            response = llamar_nvidia([
                {"role": "system", "content": "Eres un auditor de seguridad. Responde SOLO con JSON válido."},
                {"role": "user", "content": prompt}
            ], timeout=60)

            content = response.get("content", "").strip()
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
            else:
                result = json.loads(content)

            required_keys = ["approved", "reason", "risk_level", "findings"]
            if not all(k in result for k in required_keys):
                raise ValueError("Respuesta IA incompleta")

            return {
                "approved": bool(result["approved"]),
                "reason": str(result["reason"])[:500],
                "risk_level": str(result["risk_level"]),
                "findings": list(result["findings"])[:10]
            }

        except Exception as e:
            return {
                "approved": False,
                "reason": f"Error en revisión de seguridad: {str(e)[:200]}",
                "risk_level": "high",
                "findings": ["Error técnico en revisión automática"]
            }