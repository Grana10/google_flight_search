#!/usr/bin/env python3
"""
=============================================================
  Monitor de vuelos — Roadtrip California 2026
  Funciona en Mac y Windows

  Busca las 4 combinaciones reales del roadtrip:
    A) MAD→LAX  +  SFO→MAD  (open-jaw clásico, sin backtrack)
    B) MAD→SFO  +  LAX→MAD  (open-jaw al revés)
    C) MAD→LAX  +  LAX→MAD  (round-trip, solo LA)
    D) MAD→SFO  +  SFO→MAD  (round-trip, solo SF)

  Cada combinación se busca con la duración correcta (13-17 días)
  usando URLs con parámetro tfs= (protobuf) que Google Flights
  interpreta exactamente como ida+vuelta o multiciudad con fechas fijas.

  Requiere:
    pip install selenium webdriver-manager plyer beautifulsoup4 fast-flights

  Uso:
    python monitor_vuelos.py
=============================================================
"""

import time, sys, os, json, re, datetime, platform, threading, webbrowser
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from itertools import product

# ─── AUTO-INSTALL ─────────────────────────────────────────────────────────────

def _install(pkg):
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "-q"])

for _pkg, _imp in [
    ("selenium",          "selenium"),
    ("webdriver-manager", "webdriver_manager"),
    ("plyer",             "plyer"),
    ("beautifulsoup4",    "bs4"),
    ("fast-flights",      "fast_flights"),
]:
    try:
        __import__(_imp)
    except ImportError:
        print(f"📦 Instalando {_pkg}...")
        _install(_pkg)

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from plyer import notification as plyer_notification
from bs4 import BeautifulSoup
from fast_flights import FlightData, Passengers, create_filter


# ─── CONFIG — EDITA AQUÍ ──────────────────────────────────────────────────────

CONFIG = {
    # Precio máximo por persona (suma ida+vuelta), en EUR — dispara la alerta
    "precio_maximo_eur": 900,

    # Número de adultos
    "adultos": 2,

    # Rango de fechas de salida desde Madrid
    "fecha_salida_min": "2026-07-10",
    "fecha_salida_max": "2026-08-03",

    # Duración del viaje en días. Se buscan TODAS las combinaciones.
    "duracion_min_dias": 13,
    "duracion_max_dias": 17,

    # Duración máxima de cada tramo de vuelo (horas)
    # MAD-LAX directo ~12h | con 1 escala ~15-18h típico
    "max_duracion_horas": 18,

    # Intervalo entre ciclos completos de monitoreo (minutos)
    "intervalo_minutos": 30,

    # Abrir automáticamente en el navegador al detectar precio bajo
    "abrir_navegador_en_alerta": False,

    # Archivo de log JSON
    "archivo_log": "vuelos_log.json",

    # False = Chrome visible (recomendado) | True = invisible
    "headless": True,

    # Segundos máx. de espera para que carguen resultados (WebDriverWait dinámico)
    "espera_carga": 12,

    # Pausa mínima entre peticiones (segundos) — evita rate-limiting
    "pausa_entre_urls": 1,

    # Duración central para la fase rápida (días). Se usa 1 duración representativa
    # por fecha en la fase rápida; solo las fechas prometedoras se profundizan.
    "duracion_central_dias": 15,

    # Porcentaje de margen sobre el umbral para considerar una fecha "prometedora"
    # y buscar todas las duraciones. Ej: 0.15 = fechas con precio < umbral × 1.15
    "margen_fase_profunda": 0.15,
}

# ─── COMBINACIONES DE VIAJE ───────────────────────────────────────────────────
# Cada combo define los dos tramos del viaje.
# tipo: "open-jaw" (vuelo multiciudad) o "round-trip"

COMBOS = [
    {
        "id":     "LAX_SFO",
        "label":  "MAD→LAX + SFO→MAD",
        "desc":   "Llegas a LA, el roadtrip sube por la Hwy 1, vuelas de SF",
        "tipo":   "open-jaw",
        "origen_ida":    "MAD",
        "destino_ida":   "LAX",
        "origen_vuelta": "SFO",
        "destino_vuelta":"MAD",
    },
    {
        "id":     "SFO_LAX",
        "label":  "MAD→SFO + LAX→MAD",
        "desc":   "Llegas a SF, el roadtrip baja por la Hwy 1, vuelas de LA",
        "tipo":   "open-jaw",
        "origen_ida":    "MAD",
        "destino_ida":   "SFO",
        "origen_vuelta": "LAX",
        "destino_vuelta":"MAD",
    },
    {
        "id":     "LAX_LAX",
        "label":  "MAD→LAX + LAX→MAD",
        "desc":   "Round-trip Los Ángeles (haces el roadtrip y vuelves a LA)",
        "tipo":   "round-trip",
        "origen_ida":    "MAD",
        "destino_ida":   "LAX",
        "origen_vuelta": "LAX",
        "destino_vuelta":"MAD",
    },
    {
        "id":     "SFO_SFO",
        "label":  "MAD→SFO + SFO→MAD",
        "desc":   "Round-trip San Francisco (haces el roadtrip y vuelves a SF)",
        "tipo":   "round-trip",
        "origen_ida":    "MAD",
        "destino_ida":   "SFO",
        "origen_vuelta": "SFO",
        "destino_vuelta":"MAD",
    },
]


# ─── DATACLASS ────────────────────────────────────────────────────────────────

@dataclass
class Resultado:
    combo_id: str
    combo_label: str
    fecha_ida: str
    fecha_vuelta: str
    duracion_dias: int
    tipo_escalas: str          # "directo" | "1 escala"
    precio_total: float        # total para N adultos, ida+vuelta (lo que reporta Google con adults=N)
    duracion_vuelo_h: Optional[float]  # del tramo mostrado por GFlights
    aerolinea: str
    url: str
    timestamp: str = field(default_factory=lambda: datetime.datetime.now().isoformat())
    alerta_disparada: bool = False


# ─── GENERACIÓN DE COMBINACIONES DE FECHAS ────────────────────────────────────

def generar_combinaciones_fechas() -> List[tuple]:
    """
    Genera todas las parejas (fecha_ida, fecha_vuelta) dentro del rango
    configurado y la duración mínima/máxima.
    """
    d_min   = datetime.date.fromisoformat(CONFIG["fecha_salida_min"])
    d_max   = datetime.date.fromisoformat(CONFIG["fecha_salida_max"])
    dur_min = CONFIG["duracion_min_dias"]
    dur_max = CONFIG["duracion_max_dias"]
    combos  = []
    d = d_min
    while d <= d_max:
        for dur in range(dur_min, dur_max + 1):
            vuelta = d + datetime.timedelta(days=dur)
            combos.append((d.isoformat(), vuelta.isoformat(), dur))
        d += datetime.timedelta(days=1)
    return combos


# ─── CONSTRUCCIÓN DE URL CON TFS (PROTOBUF) ───────────────────────────────────

def make_tfs_url(combo: dict, fecha_ida: str, fecha_vuelta: str,
                 max_stops: int = 1) -> str:
    """
    Genera la URL de Google Flights con el parámetro tfs= correcto,
    que garantiza la búsqueda del tipo de viaje exacto (open-jaw o round-trip)
    con las fechas de ida Y vuelta especificadas.

    max_stops:
      0 = solo directos
      1 = máx. 1 escala
    """
    adultos = CONFIG["adultos"]
    trip_type = "multi-city" if combo["tipo"] == "open-jaw" else "round-trip"

    flight_data = [
        FlightData(date=fecha_ida,
                   from_airport=combo["origen_ida"],
                   to_airport=combo["destino_ida"]),
        FlightData(date=fecha_vuelta,
                   from_airport=combo["origen_vuelta"],
                   to_airport=combo["destino_vuelta"]),
    ]

    tfs_obj = create_filter(
        flight_data=flight_data,
        trip=trip_type,
        seat="economy",
        passengers=Passengers(adults=adultos),
        max_stops=max_stops,
    )
    tfs = tfs_obj.as_b64()
    if isinstance(tfs, bytes):
        tfs = tfs.decode()
    tfs = tfs.strip("b'\"")

    return (
        f"https://www.google.com/travel/flights/search"
        f"?tfs={tfs}"
        f"&hl=es&curr=EUR"
        f"&adults={adultos}"
        f"&travelers={adultos}adults"
    )


# ─── CHROME ───────────────────────────────────────────────────────────────────

def crear_driver():
    opts = Options()
    if CONFIG["headless"]:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument("--lang=es-ES")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    perfil = os.path.join(os.path.expanduser("~"), ".monitor_vuelos_profile")
    os.makedirs(perfil, exist_ok=True)
    opts.add_argument(f"--user-data-dir={perfil}")
    service = Service(ChromeDriverManager().install())
    driver  = webdriver.Chrome(service=service, options=opts)
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver


# ─── CONSENTIMIENTO GOOGLE ────────────────────────────────────────────────────

def aceptar_consentimiento_si_aparece(driver) -> bool:
    try:
        body = driver.find_element(By.TAG_NAME, "body").text.lower()
    except Exception:
        body = ""
    hay_consent = (
        "consent.google.com" in driver.current_url
        or any(kw in body for kw in ["antes de continuar", "before you continue",
                                     "acepta las condiciones", "accept google"])
    )
    if not hay_consent:
        return False
    print("   🍪  Consentimiento detectado — aceptando...")
    selectores = [
        "//button[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'aceptar todo')]",
        "//button[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'accept all')]",
        "//button[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'acepto')]",
        "//button[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'agree')]",
        "//*[@id='L2AGLb']", "//*[@id='VnjCcb']", "//*[@id='W0wltc']",
    ]
    for sel in selectores:
        try:
            btn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, sel)))
            btn.click()
            print("   ✅  Consentimiento aceptado.")
            time.sleep(3)
            return True
        except Exception:
            continue
    try:
        for btn in driver.find_elements(By.TAG_NAME, "button"):
            txt = btn.text.strip().lower()
            if any(p in txt for p in ["aceptar","acepto","accept","agree","continuar"]) \
                    and btn.is_displayed():
                btn.click()
                time.sleep(3)
                return True
    except Exception:
        pass
    if not CONFIG["headless"]:
        print("   ⚠️  Acéptalo manualmente. Esperando 15s...")
        time.sleep(15)
    return False


# ─── PARSEO DEL ARIA-LABEL ────────────────────────────────────────────────────

def _parsear_aria(aria: str):
    """
    Extrae precio (por persona i+v), duración del tramo mostrado,
    número de escalas y aerolínea del aria-label de un bloque li.pIav2d.

    Nota: con búsquedas tfs= de round-trip/multiciudad, el aria-label
    del PRIMER tramo (ida) describe ese tramo. El precio es el total i+v
    por persona.
    """
    aria = aria.replace('\xa0', ' ')

    precio = None
    m = re.search(r'A partir de ([\d.,]+)\s*euro', aria, re.I)
    if m:
        try: precio = float(m.group(1).replace('.','').replace(',','.'))
        except ValueError: pass

    dur_h = None
    m = re.search(r'Duración total:\s*(\d+)\s*h(?:\s*(\d+)\s*min)?', aria, re.I)
    if m:
        dur_h = round(int(m.group(1)) + (int(m.group(2))/60 if m.group(2) else 0), 2)

    escalas = None
    if re.search(r'Vuelo directo', aria, re.I):
        escalas = 0
    else:
        m = re.search(r'Vuelo con (\d+)\s*escala', aria, re.I)
        if m: escalas = int(m.group(1))

    aerolinea = ""
    m = re.search(r'Vuelo(?:\s+con\s+\d+\s+escalas?)?\s+(?:directo\s+)?de\s+([^.]+?)\.', aria, re.I)
    if m: aerolinea = m.group(1).strip()

    return precio, dur_h, escalas, aerolinea


# ─── SCRAPER ──────────────────────────────────────────────────────────────────

def scrape(driver, url: str, combo: dict, fecha_ida: str,
           fecha_vuelta: str, dur_dias: int) -> Dict[str, List[Resultado]]:
    """
    Navega a la URL tfs= de Google Flights (round-trip o multi-city con
    fechas exactas). Extrae resultados y los clasifica en directos y 1 escala.
    Devuelve {"directo": [...], "1 escala": [...]}.
    """
    out: Dict[str, List[Resultado]] = {"directo": [], "1 escala": []}
    max_dur = CONFIG["max_duracion_horas"]
    vistos  = set()

    try:
        driver.get(url)
        # Sin sleep fijo — esperamos dinámicamente a que aparezcan resultados
        aceptar_consentimiento_si_aparece(driver)
        if "consent" in driver.current_url:
            time.sleep(3)
            aceptar_consentimiento_si_aparece(driver)

        try:
            WebDriverWait(driver, CONFIG["espera_carga"]).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "li.pIav2d"))
            )
        except Exception:
            # Si no aparece el selector, espera breve y sigue
            time.sleep(3)

        soup   = BeautifulSoup(driver.page_source, "html.parser")
        bloques = soup.select("li.pIav2d")

        for bloque in bloques:
            aria = ""
            for el in bloque.find_all(attrs={"aria-label": True}):
                al = el.get("aria-label", "")
                if "euro" in al.lower():
                    aria = al
                    break
            if not aria:
                continue

            precio, dur_h, escalas, aerolinea = _parsear_aria(aria)
            if precio is None or escalas is None:
                continue

            clave_dup = f"{precio}|{dur_h}|{escalas}"
            if clave_dup in vistos:
                continue
            vistos.add(clave_dup)

            if dur_h is not None and dur_h > max_dur:
                continue

            if escalas == 0:
                tipo = "directo"
            elif escalas == 1:
                tipo = "1 escala"
            else:
                continue

            out[tipo].append(Resultado(
                combo_id=combo["id"],
                combo_label=combo["label"],
                fecha_ida=fecha_ida,
                fecha_vuelta=fecha_vuelta,
                duracion_dias=dur_dias,
                tipo_escalas=tipo,
                precio_total=precio,
                duracion_vuelo_h=dur_h,
                aerolinea=aerolinea,
                url=url,
            ))

    except Exception as e:
        print(f"   ❌  Error: {e}")

    return out


# ─── RESUMEN LEGIBLE ──────────────────────────────────────────────────────────

def imprimir_resumen(mejores: Dict[str, Optional[Resultado]]):
    adultos = CONFIG["adultos"]
    max_p   = CONFIG["precio_maximo_eur"]
    ahora   = datetime.datetime.now().strftime("%H:%M")

    def fmt(r):
        pp     = r.precio_total / adultos
        fechas = f"{r.fecha_ida[5:]} → {r.fecha_vuelta[5:]}"
        alerta = " 🚨" if pp < max_p else ""
        return f"    • {r.combo_label}  {fechas}  —  {pp:.0f}€/pers  ({r.precio_total:.0f}€ total){alerta}"

    # Recoger todos los resultados disponibles separados por tipo
    directos = sorted(
        [r for r in (mejores.get(f"{c['id']}|directo") for c in COMBOS) if r],
        key=lambda r: r.precio_total
    )
    con_escala = sorted(
        [r for r in (mejores.get(f"{c['id']}|1 escala") for c in COMBOS) if r],
        key=lambda r: r.precio_total
    )

    lineas = ["", f"  ✈  RESUMEN  {ahora}  (umbral alerta: {max_p}€/pers)", ""]

    lineas.append("  Las mejores opciones directas son:")
    if directos:
        lineas += [fmt(r) for r in directos]
    else:
        lineas.append("    • Sin datos de vuelos directos.")

    lineas.append("")
    lineas.append("  Las mejores opciones con 1 escala son:")
    if con_escala:
        lineas += [fmt(r) for r in con_escala]
    else:
        lineas.append("    • Sin datos de vuelos con escala.")

    lineas.append("")
    texto = "\n".join(lineas)
    print(texto)

    fname = f"resumen_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(fname, "w", encoding="utf-8") as f:
        f.write(texto)
    print(f"  💾  {fname}\n")


# ─── NOTIFICACIONES ───────────────────────────────────────────────────────────

def notificar(titulo: str, mensaje: str):
    try:
        plyer_notification.notify(title=titulo, message=mensaje,
                                  app_name="Monitor Vuelos", timeout=15)
        return
    except Exception:
        pass
    if platform.system() == "Darwin":
        os.system(f"osascript -e 'display notification \"{mensaje}\" "
                  f"with title \"{titulo}\" sound name \"Ping\"'")
    elif platform.system() == "Windows":
        os.system(f'powershell -Command "msg * /TIME:10 \"{titulo}: {mensaje}\""')

def alerta_sonora():
    try:
        if platform.system() == "Darwin":
            os.system("afplay /System/Library/Sounds/Glass.aiff")
        elif platform.system() == "Windows":
            import winsound
            for _ in range(3):
                winsound.Beep(1000, 400); time.sleep(0.2)
        else:
            print("\a\a\a")
    except Exception:
        print("\a")


# ─── LOG ──────────────────────────────────────────────────────────────────────

def guardar_log(r: Resultado):
    registros = []
    if os.path.exists(CONFIG["archivo_log"]):
        with open(CONFIG["archivo_log"], "r", encoding="utf-8") as f:
            try: registros = json.load(f)
            except Exception: pass
    registros.append(r.__dict__)
    with open(CONFIG["archivo_log"], "w", encoding="utf-8") as f:
        json.dump(registros, f, ensure_ascii=False, indent=2)

def mostrar_alertas_historicas():
    if not os.path.exists(CONFIG["archivo_log"]): return
    with open(CONFIG["archivo_log"], "r", encoding="utf-8") as f:
        try: registros = json.load(f)
        except Exception: return
    alertas = [r for r in registros if r.get("alerta_disparada")]
    if not alertas: return
    print(f"\n  📋  Alertas históricas ({len(alertas)}, últimas 6):")
    for a in alertas[-6:]:
        dur = f"{a['duracion_vuelo_h']:.1f}h" if a.get("duracion_vuelo_h") else "?"
        print(f"     • {a['combo_label']}  {a['fecha_ida']}→{a['fecha_vuelta']}"
              f"  [{a['tipo_escalas']}]  {a['precio_total']:.0f}€/pers  {dur}"
              f"  [{a['timestamp'][:16]}]")


# ─── CICLO PRINCIPAL ──────────────────────────────────────────────────────────

def _procesar_resultado(por_tipo, combo, fecha_ida, fecha_vuelta, dur_dias,
                        mejores, alertas, max_p, url, fase):
    """Procesa el resultado de un scrape: actualiza mejores, dispara alertas."""
    partes = [f"     {fecha_ida}→{fecha_vuelta} ({dur_dias}d) [{fase}]"]
    for tipo in ["directo", "1 escala"]:
        vuelos = por_tipo.get(tipo, [])
        clave  = f"{combo['id']}|{tipo}"
        if not vuelos:
            partes.append(f"  [{tipo}: —]")
            continue
        mejor = min(vuelos, key=lambda v: v.precio_total)
        if mejores[clave] is None or mejor.precio_total < mejores[clave].precio_total:
            mejores[clave] = mejor
        dur_txt = f"{mejor.duracion_vuelo_h:.1f}h" if mejor.duracion_vuelo_h else "?h"
        sim     = "🟢" if mejor.precio_total / CONFIG["adultos"] < max_p else "🔴"
        partes.append(f"  {sim} {tipo}: {mejor.precio_total:.0f}€ {dur_txt}")
        guardar_log(mejor)
        if mejor.precio_total / CONFIG["adultos"] < max_p:
            mejor.alerta_disparada = True
            alertas.append(mejor)
            titulo = f"✈ {combo['label']} [{tipo}]"
            msg    = (f"{fecha_ida}→{fecha_vuelta}  {mejor.precio_total/CONFIG['adultos']:.0f}€/pers  ({mejor.precio_total:.0f}€ total)"
                      f"  ({dur_txt})  —  umbral {max_p}€")
            print(f"\n   🚨  ALERTA — {msg}\n")
            notificar(titulo, msg)
            threading.Thread(target=alerta_sonora, daemon=True).start()
            if CONFIG["abrir_navegador_en_alerta"]:
                webbrowser.open(url)
    print("".join(partes))


def ciclo_completo(driver) -> List[Resultado]:
    """
    Estrategia en DOS FASES para minimizar el número de peticiones:

    FASE 1 — RÁPIDA: busca solo la duración central (ej. 15 días) para cada
    fecha de salida. Identifica qué fechas tienen precios prometedores
    (por debajo del umbral × (1 + margen)).

    FASE 2 — PROFUNDA: solo para las fechas prometedoras de la fase 1,
    busca el resto de duraciones (13, 14, 16, 17 días) para encontrar
    el precio óptimo exacto.

    Reducción típica: de 500 URLs a ~120-200 URLs por ciclo.
    """
    dur_min     = CONFIG["duracion_min_dias"]
    dur_max     = CONFIG["duracion_max_dias"]
    dur_central = CONFIG["duracion_central_dias"]
    margen      = CONFIG["margen_fase_profunda"]
    max_p       = CONFIG["precio_maximo_eur"]
    umbral_fase2 = max_p * (1 + margen)

    # Fechas de salida únicas (sin duplicar por duración)
    d_min = datetime.date.fromisoformat(CONFIG["fecha_salida_min"])
    d_max = datetime.date.fromisoformat(CONFIG["fecha_salida_max"])
    fechas_salida = []
    d = d_min
    while d <= d_max:
        fechas_salida.append(d.isoformat())
        d += datetime.timedelta(days=1)

    duraciones_extra = [d for d in range(dur_min, dur_max + 1) if d != dur_central]

    alertas = []
    mejores: Dict[str, Optional[Resultado]] = {}
    for combo in COMBOS:
        for tipo in ["directo", "1 escala"]:
            mejores[f"{combo['id']}|{tipo}"] = None

    n_fase1 = len(fechas_salida) * len(COMBOS)
    print(f"\n{'─'*72}")
    print(f"  🔍  {datetime.datetime.now().strftime('%H:%M:%S')}")
    print(f"  FASE 1: {n_fase1} URLs  ({len(fechas_salida)} fechas × {len(COMBOS)} combos, duración {dur_central}d)")
    print(f"  FASE 2: solo fechas con precio < {umbral_fase2:.0f}€ × {len(duraciones_extra)} duraciones extra")
    print(f"{'─'*72}")

    # ── FASE 1: duración central ──────────────────────────────────────────────
    fechas_prometedoras: Dict[str, set] = {c["id"]: set() for c in COMBOS}

    for combo in COMBOS:
        print(f"\n  🗺  {combo['label']}  [FASE 1 — {dur_central}d]")
        for fecha_ida in fechas_salida:
            fecha_vuelta = (datetime.date.fromisoformat(fecha_ida)
                            + datetime.timedelta(days=dur_central)).isoformat()
            url      = make_tfs_url(combo, fecha_ida, fecha_vuelta)
            por_tipo = scrape(driver, url, combo, fecha_ida, fecha_vuelta, dur_central)

            if not any(por_tipo.values()):
                print(f"     {fecha_ida}→{fecha_vuelta}  —  sin resultados")
                time.sleep(CONFIG["pausa_entre_urls"])
                continue

            _procesar_resultado(por_tipo, combo, fecha_ida, fecha_vuelta,
                                dur_central, mejores, alertas, max_p, url, "F1")

            for tipo in ["directo", "1 escala"]:
                vuelos = por_tipo.get(tipo, [])
                if vuelos and min(v.precio_total / CONFIG["adultos"] for v in vuelos) < umbral_fase2:
                    fechas_prometedoras[combo["id"]].add(fecha_ida)

            time.sleep(CONFIG["pausa_entre_urls"])

    # ── Entre fases: listener no bloqueante ─────────────────────────────────
    total_f2 = sum(len(v) for v in fechas_prometedoras.values()) * len(duraciones_extra)

    # threading.Event compartido: cuando se activa, la fase 2 se para en la
    # próxima iteración. El comando es 'fin' para que sea inconfundible con
    # cualquier tecla accidental (Enter, Ctrl+C, etc.)
    _saltar = threading.Event()

    def _listener():
        while not _saltar.is_set():
            try:
                if input().strip().lower() == "fin":
                    _saltar.set()
                    print("\n  ⏭  'fin' recibido — saltando al resumen.\n")
                    break
            except EOFError:
                break

    if total_f2 == 0:
        print(f"\n  ✅  Fase 2: sin fechas prometedoras, se omite.")
    else:
        print(f"\n{'─'*72}")
        print(f"  ✅  FASE 1 completada  —  Fase 2 tiene {total_f2} búsquedas adicionales.")
        print(f"  Escribe  fin  + Enter en cualquier momento para saltar al resumen.")
        print(f"  (Fase 2 arranca en 5 segundos — escribe 'fin' ahora para omitirla por completo)")
        print(f"{'─'*72}")

        hilo = threading.Thread(target=_listener, daemon=True)
        hilo.start()
        for _ in range(5):
            if _saltar.is_set():
                break
            time.sleep(1)

    # ── FASE 2: duraciones extra solo para fechas prometedoras ───────────────
    if total_f2 > 0 and not _saltar.is_set():
        print(f"\n{'─'*72}")
        print(f"  FASE 2: {total_f2} URLs adicionales (duraciones {duraciones_extra}d)")
        print(f"{'─'*72}")
        for combo in COMBOS:
            prometedoras = sorted(fechas_prometedoras[combo["id"]])
            if not prometedoras:
                continue
            print(f"\n  🗺  {combo['label']}  [FASE 2 — {len(prometedoras)} fechas × {len(duraciones_extra)} durs]")
            for fecha_ida in prometedoras:
                for dur in duraciones_extra:
                    if _saltar.is_set():
                        break
                    fecha_vuelta = (datetime.date.fromisoformat(fecha_ida)
                                    + datetime.timedelta(days=dur)).isoformat()
                    url      = make_tfs_url(combo, fecha_ida, fecha_vuelta)
                    por_tipo = scrape(driver, url, combo, fecha_ida, fecha_vuelta, dur)
                    if not any(por_tipo.values()):
                        time.sleep(CONFIG["pausa_entre_urls"])
                        continue
                    _procesar_resultado(por_tipo, combo, fecha_ida, fecha_vuelta,
                                        dur, mejores, alertas, max_p, url, "F2")
                    time.sleep(CONFIG["pausa_entre_urls"])
                if _saltar.is_set():
                    break

    imprimir_resumen(mejores)
    return alertas


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    fechas = generar_combinaciones_fechas()

    print("=" * 72)
    print("  ✈  MONITOR DE VUELOS — ROADTRIP CALIFORNIA 2026")
    print("=" * 72)
    print(f"  Precio máx. por persona (i+v):  {CONFIG['precio_maximo_eur']}€")
    print(f"  Adultos:                         {CONFIG['adultos']}")
    print(f"  Salidas desde MAD:               {CONFIG['fecha_salida_min']} → {CONFIG['fecha_salida_max']}")
    print(f"  Duración viaje:                  {CONFIG['duracion_min_dias']}-{CONFIG['duracion_max_dias']} días")
    print(f"  Duración máx. tramo:             {CONFIG['max_duracion_horas']}h")
    print(f"  Combinaciones de fechas:         {len(fechas)}")
    print(f"  Combinaciones de viaje:          {len(COMBOS)}")
    print(f"  Total búsquedas por ciclo:       {len(COMBOS) * len(fechas)}")
    print(f"  Tipo de URL:                     tfs= (protobuf — fecha ida+vuelta exacta)")
    print(f"  Combos cubiertos:")
    for c in COMBOS:
        print(f"    {'open-jaw' if c['tipo']=='open-jaw' else 'round-trip'}  {c['label']}")
    print(f"  Intervalo:  {CONFIG['intervalo_minutos']} min  |  Headless: {CONFIG['headless']}")
    print("=" * 72)
    print("""
  ℹ️   Perfil de Chrome persistente en ~/.monitor_vuelos_profile
       El consentimiento de Google se acepta solo la primera vez.
    """)

    mostrar_alertas_historicas()

    print("  🚀  Iniciando Chrome...\n")
    try:
        driver = crear_driver()
    except Exception as e:
        print(f"  ❌  Error al iniciar Chrome: {e}")
        print("  pip install selenium webdriver-manager plyer beautifulsoup4 fast-flights")
        sys.exit(1)

    total_alertas = 0
    iteracion     = 0

    try:
        while True:
            iteracion += 1
            print(f"  📡  Ciclo #{iteracion}")
            alertas        = ciclo_completo(driver)
            total_alertas += len(alertas)
            proxima = datetime.datetime.now() + datetime.timedelta(minutes=CONFIG["intervalo_minutos"])
            print(f"  ⏱  Próximo ciclo: {proxima.strftime('%H:%M:%S')}"
                  f"  |  Alertas esta sesión: {total_alertas}")
            print("  (Ctrl+C para detener)\n")
            time.sleep(CONFIG["intervalo_minutos"] * 60)

    except KeyboardInterrupt:
        print("\n\n  🛑  Monitor detenido.")
        print(f"  📊  Alertas disparadas: {total_alertas}")
        mostrar_alertas_historicas()
    finally:
        driver.quit()
        print("  👋  Chrome cerrado.")

if __name__ == "__main__":
    main()
