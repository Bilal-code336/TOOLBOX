"""
Genera una página por idioma a partir de index.tpl.html + diccionarios.

  python i18n/build_i18n.py   →  index.html (es), en/index.html, fr/index.html y sitemap.xml en la raíz del repositorio

Para AÑADIR un idioma:
  1. Copia lang_en.py a lang_xx.py y traduce los textos.
  2. Añádelo a LANGS (abajo) y, si quieres bandera, a FLAG_SPRITE.
  3. Vuelve a ejecutar este script y añade la ruta en TOOLBOXMOTOR.py.
"""
import html as htmllib
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Por defecto escribe en la carpeta superior a i18n/ (la raíz de tu repositorio)
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(HERE)
BASE = 'https://toolbox-zci6.onrender.com'
LASTMOD = '2026-10-16'

LANGS = {
    'es': {'name': 'Español', 'code': 'ES', 'path': '/', 'file': 'index.html', 'og': 'es_ES', 'label': 'Idioma'},
    'en': {'name': 'English', 'code': 'EN', 'path': '/en/', 'file': 'en/index.html', 'og': 'en_US', 'label': 'Language'},
    'fr': {'name': 'Français', 'code': 'FR', 'path': '/fr/', 'file': 'fr/index.html', 'og': 'fr_FR', 'label': 'Langue'},
}

ES_META = {
    'title': 'TOOLBOX — Herramientas online gratis',
    'desc': 'TOOLBOX reúne herramientas online gratis para estudiar, escribir, calcular, trabajar con imágenes y usar inteligencia artificial. Humanizador de texto, corrector, Pomodoro, OCR y mucho más.',
    'ld_desc': 'TOOLBOX reúne herramientas online gratis para estudiar, escribir, calcular, trabajar con imágenes y usar inteligencia artificial.',
    'es_tag_title': 'Español',
}

FLAG_SPRITE = '''<svg width="0" height="0" style="position:absolute" aria-hidden="true" focusable="false">
<defs>
<g id="flag-es"><rect width="24" height="16" fill="#AA151B"/><rect y="4" width="24" height="8" fill="#F1BF00"/></g>
<g id="flag-fr"><rect width="24" height="16" fill="#ED2939"/><rect width="16" height="16" fill="#FFFFFF"/><rect width="8" height="16" fill="#002395"/></g>
<clipPath id="flag-en-clip"><path d="M12,8 h12 v8 z v8 h-12 z h-12 v-8 z v-8 h12 z"/></clipPath>
<g id="flag-en"><rect width="24" height="16" fill="#012169"/>
<path d="M0,0 L24,16 M24,0 L0,16" stroke="#FFFFFF" stroke-width="2.6"/>
<path d="M0,0 L24,16 M24,0 L0,16" stroke="#C8102E" stroke-width="1.5" clip-path="url(#flag-en-clip)"/>
<path d="M12,0 V16 M0,8 H24" stroke="#FFFFFF" stroke-width="4.4"/>
<path d="M12,0 V16 M0,8 H24" stroke="#C8102E" stroke-width="2.6"/></g>
</defs>
</svg>'''


def flag(code):
    return '<svg class="flag" viewBox="0 0 24 16" aria-hidden="true" focusable="false"><use href="#flag-%s"/></svg>' % code


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + '.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


tpl = open(os.path.join(HERE, 'index.tpl.html'), encoding='utf-8').read()
es_js = load('lang_es').JS
dicts = {'es': {'META': ES_META, 'HTML': {}, 'JS': es_js}}
for code in ('en', 'fr'):
    m = load('lang_' + code)
    dicts[code] = {'META': m.META, 'HTML': m.HTML, 'JS': m.JS}

# ---- comprobaciones de cobertura de las claves del JavaScript ----
problems = []
for code in ('en', 'fr'):
    missing = set(es_js) - set(dicts[code]['JS'])
    extra = set(dicts[code]['JS']) - set(es_js)
    if missing:
        problems.append('%s: faltan claves JS %s' % (code, sorted(missing)))
    if extra:
        problems.append('%s: sobran claves JS %s' % (code, sorted(extra)))
    # los marcadores {x} deben ser los mismos que en español
    for k, v in es_js.items():
        a = sorted(set(re.findall(r'\{(\w+)\}', v)))
        b = sorted(set(re.findall(r'\{(\w+)\}', dicts[code]['JS'].get(k, ''))))
        if a != b:
            problems.append('%s: marcadores distintos en %s: %s vs %s' % (code, k, a, b))


def norm(t):
    return re.sub(r'\s+', ' ', t).strip()


ALLOWED_UNTRANSLATED = re.compile(r'^([^A-Za-zÁ-ú]*|ES|C|[÷×])$')


def translate_html(doc, code):
    """Traduce nodos de texto y atributos del HTML (no toca <script> ni <style>)."""
    table = dicts[code]['HTML']
    missing = []

    parts = re.split(r'(<script[\s\S]*?</script>|<style[\s\S]*?</style>)', doc)
    out = []
    for part in parts:
        if part.startswith('<script') or part.startswith('<style'):
            out.append(part)
            continue

        def text_repl(m):
            raw = m.group(1)
            n = norm(raw)
            if not n or ALLOWED_UNTRANSLATED.match(n) or '{{' in n:
                return m.group(0)
            if n in table:
                lead = raw[:len(raw) - len(raw.lstrip())]
                trail = raw[len(raw.rstrip()):]
                return '>' + lead + htmllib.escape(table[n], quote=False) + trail + '<'
            missing.append(('texto', n))
            return m.group(0)

        part = re.sub(r'>([^<>]+)<', text_repl, part)

        def attr_repl(m):
            n = norm(m.group(2))
            if not n or '{{' in n or n == dicts[code]['META']['es_tag_title']:
                return m.group(0)
            if n in table:
                return '%s="%s"' % (m.group(1), htmllib.escape(table[n], quote=True))
            missing.append((m.group(1), n))
            return m.group(0)

        part = re.sub(r'\b(placeholder|title|aria-label|alt|data-name)="([^"]*)"', attr_repl, part)
        out.append(part)
    return ''.join(out), missing



# ---- rótulos de estilos (negrita, cursiva…) escritos con las propias letras Unicode ----
def _rng(upper, lower, exc=None):
    exc = exc or {}
    def f(text):
        out = ''
        for ch in text:
            if 'A' <= ch <= 'Z':
                out += exc.get(ch, chr(upper + ord(ch) - 65))
            elif 'a' <= ch <= 'z':
                out += exc.get(ch, chr(lower + ord(ch) - 97))
            else:
                out += ch
        return out
    return f


STYLERS = {
    'bold': _rng(0x1D5D4, 0x1D5EE),
    'italic': _rng(0x1D608, 0x1D622),
    'bolditalic': _rng(0x1D63C, 0x1D656),
    'gothic': _rng(0x1D504, 0x1D51E, {'C': '\u212d', 'H': '\u210c', 'I': '\u2111', 'R': '\u211c', 'Z': '\u2128'}),
    'double': _rng(0x1D538, 0x1D552, {'C': '\u2102', 'H': '\u210d', 'N': '\u2115', 'P': '\u2119', 'Q': '\u211a', 'R': '\u211d', 'Z': '\u2124'}),
    'mono': _rng(0x1D670, 0x1D68A),
}

STYLE_LABELS = {
    'en': {'bold': 'Bold', 'italic': 'Italic', 'bolditalic': 'Bold italic', 'gothic': 'Gothic', 'double': 'Double', 'mono': 'Mono'},
    'fr': {'bold': 'Gras', 'italic': 'Italique', 'bolditalic': 'Gras italique', 'gothic': 'Gothique', 'double': 'Double', 'mono': 'Mono'},
}


def translate_style_buttons(doc, code):
    def repl(m):
        return m.group(1) + STYLERS[m.group(2)](STYLE_LABELS[code][m.group(2)]) + m.group(4)
    doc, n = re.subn(r"(<button onclick=\"aplicarEstilo\('(\w+)'\)\">)(\s*)[^<]*?(\s*</button>)", lambda m: m.group(1) + m.group(3) + STYLERS[m.group(2)](STYLE_LABELS[code][m.group(2)]) + m.group(4), doc)
    assert n == 6, n
    return doc


def switch_html(current):
    items = ''
    for code, cfg in LANGS.items():
        cur = ' aria-current="true"' if code == current else ''
        check = '<span class="check" aria-hidden="true">✓</span>' if code == current else ''
        items += ('<li><a href="%s" hreflang="%s" lang="%s" data-lang="%s"%s>%s<span>%s</span>%s</a></li>'
                  % (cfg['path'], code, code, code, cur, flag(code), cfg['name'], check))
    c = LANGS[current]
    return ('<div class="lang-switch" id="langSwitch">'
            '<button type="button" class="lang-btn" id="langBtn" aria-haspopup="true" aria-expanded="false" aria-controls="langMenu" '
            'aria-label="%s: %s" title="Idioma / Language / Langue">%s<span>%s</span><span class="caret" aria-hidden="true">▾</span></button>'
            '<ul class="lang-menu" id="langMenu" hidden>%s</ul></div>' % (c['label'], c['name'], flag(current), c['code'], items))


def footer_langs(current):
    links = ''.join('<a href="%s" hreflang="%s" lang="%s">%s<span>%s</span></a>' % (cfg['path'], code, code, flag(code), cfg['name'])
                    for code, cfg in LANGS.items())
    return '<div class="footer-langs" aria-label="Idiomas / Languages / Langues">%s</div>' % links


def alternates():
    lines = ['<link rel="alternate" hreflang="%s" href="%s%s">' % (code, BASE, cfg['path']) for code, cfg in LANGS.items()]
    lines.append('<link rel="alternate" hreflang="x-default" href="%s/">' % BASE)
    return '\n'.join(lines)


def js_json(obj):
    return json.dumps(obj, ensure_ascii=False).replace('</', '<\\/').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')


all_missing = {}
for code, cfg in LANGS.items():
    meta = dicts[code]['META']
    doc = tpl

    # 1) marca de "página en español" en enlaces que aún no están traducidos
    tag = '' if code == 'es' else '<span class="lang-tag" title="%s">ES</span>' % htmllib.escape(meta['es_tag_title'])
    doc = doc.replace('{{ES_TAG}}', tag)

    # 2) traducción de los textos de la página
    if code != 'es':
        doc = translate_style_buttons(doc, code)
        doc, missing = translate_html(doc, code)
        all_missing[code] = missing

    # 3) marcadores
    doc = (doc.replace('{{FLAG_SPRITE}}', FLAG_SPRITE)
              .replace('{{LANG_SWITCH}}', switch_html(code))
              .replace('{{FOOTER_LANGS}}', footer_langs(code))
              .replace('{{ALTERNATES}}', alternates())
              .replace('{{HOME}}', cfg['path'])
              .replace('{{CANONICAL}}', BASE + cfg['path'])
              .replace('{{OG_LOCALE}}', cfg['og'])
              .replace('{{META_TITLE}}', htmllib.escape(meta['title']))
              .replace('{{META_DESC}}', htmllib.escape(meta['desc'], quote=True))
              .replace('{{LD_DESC}}', meta['ld_desc'].replace('"', '\\"'))
              .replace('{{LANG_JSON}}', js_json(code))
              .replace('{{I18N_JSON}}', js_json(dicts[code]['JS']))
              .replace('{{LANG}}', code))
    assert '{{' not in doc, re.findall(r'\{\{[^}]*\}\}', doc)[:5]

    path = os.path.join(OUT, cfg['file'])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'w', encoding='utf-8').write(doc)
    print('✔', cfg['file'], '(%d KB)' % (len(doc.encode('utf-8')) // 1024))

# ---- sitemap con hreflang ----
other = ['/TOOLBOXAI', '/blog.html', '/privacidad.html', '/terminos.html', '/cookies.html', '/contacto.html']
sm = ['<?xml version="1.0" encoding="UTF-8"?>',
      '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">']
for code, cfg in LANGS.items():
    sm.append('  <url>\n    <loc>%s%s</loc>\n    <lastmod>%s</lastmod>' % (BASE, cfg['path'], LASTMOD))
    for c2, g in LANGS.items():
        sm.append('    <xhtml:link rel="alternate" hreflang="%s" href="%s%s"/>' % (c2, BASE, g['path']))
    sm.append('    <xhtml:link rel="alternate" hreflang="x-default" href="%s/"/>\n  </url>' % BASE)
for p in other:
    sm.append('  <url>\n    <loc>%s%s</loc>\n    <lastmod>%s</lastmod>\n  </url>' % (BASE, p, LASTMOD))
sm.append('</urlset>')
open(os.path.join(OUT, 'sitemap.xml'), 'w', encoding='utf-8').write('\n'.join(sm) + '\n')
print('✔ sitemap.xml')

# ---- informe de errores ----
for code, miss in all_missing.items():
    uniq = list(dict.fromkeys(miss))
    if uniq:
        problems.append('%s: %d textos sin traducir: %s' % (code, len(uniq), uniq[:12]))
if problems:
    print('\n⚠️  PROBLEMAS:')
    for p in problems:
        print(' -', p)
    sys.exit(1)
print('Todo traducido ✓')
