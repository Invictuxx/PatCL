import binascii
from datetime import datetime
import json
import os
from pathlib import Path
from dotenv import load_dotenv
from typing import Optional
from cachetools import TTLCache
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
import httpx
import uvicorn

app = FastAPI(title="Consulta PATENTE CL")

# 1. Configuración y Claves Criptográficas
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)
RAW_KEY = os.getenv("AES_KEY")
RAW_IV = os.getenv("AES_IV")
KEY = RAW_KEY.encode("utf-8")
IV = RAW_IV.encode("utf-8")
UID_AUTH = os.getenv("UID_AUTH")
BASE_URL = os.getenv("BASE_URL")
HOST = os.getenv("HOST")

HEADERS = {
    "Host": HOST,
    "User-Agent": "invictuxxx/6.7",
    "Accept-Encoding": "gzip",
    "uid": UID_AUTH,
    "access-control-allow-origin": "*",
    "content-type": "text/plain",
}

# Caché en memoria para 1000 consultas durante 6 horas
cache = TTLCache(maxsize=1000, ttl=21600)
http_client: Optional[httpx.AsyncClient] = None


@app.on_event("startup")
async def startup_event():
    global http_client
    http_client = httpx.AsyncClient(timeout=10.0)


@app.on_event("shutdown")
async def shutdown_event():
    global http_client
    if http_client:
        await http_client.aclose()


def decrypt_hex_payload(hex_str: str) -> dict:
    clean_hex = hex_str.strip().replace("\n", "").replace("\r", "").replace(" ", "")
    cipher_bytes = binascii.unhexlify(clean_hex)

    cipher = Cipher(algorithms.AES(KEY), modes.CBC(IV), backend=default_backend())
    decryptor = cipher.decryptor()
    decrypted_bytes = decryptor.update(cipher_bytes) + decryptor.finalize()

    raw_text = decrypted_bytes.decode("utf-8", errors="ignore")
    first_key = raw_text.find('"ppu"')
    if first_key == -1:
        raise ValueError("Payload inválido")

    clean_json_str = "{" + raw_text[first_key:]
    last_brace = clean_json_str.rfind("}")
    if last_brace != -1:
        clean_json_str = clean_json_str[: last_brace + 1]

    return json.loads(clean_json_str)


def generate_html_card(data: dict) -> str:
    ppu_text = data.get("ppu", "")
    detalle = data.get("detallevehiculo") or {}
    revision = data.get("revisiononline") or {}
    propietarios = data.get("propietario") or []
    tasaciones = data.get("tasacion") or []
    historial_km = data.get("historialkilometraje") or []
    rematado = data.get("rematado")
    multas = data.get("multasrc")

    if len(ppu_text) == 6:
        ppu_display = f"{ppu_text[:2]}·{ppu_text[2:4]}  {ppu_text[4:]}"
    else:
        ppu_display = ppu_text

    # Alerta de Vencimiento de Revisión Técnica
    rt_vencida = False
    fechavenc = revision.get("fechavencimiento", "")
    if fechavenc:
        try:
            venc_date = datetime.strptime(fechavenc, "%d-%m-%Y")
            rt_vencida = venc_date < datetime.now()
        except ValueError:
            pass

    # Preparar datos del gráfico de kilometraje (orden cronológico ascendente)
    sorted_km = sorted(
        historial_km,
        key=lambda x: datetime.strptime(x["fecharevision"], "%d-%m-%Y")
        if x.get("fecharevision")
        else datetime.min,
    )
    chart_labels = json.dumps([x.get("fecharevision", "") for x in sorted_km])
    chart_data = json.dumps([int(x.get("kilometraje", 0)) for x in sorted_km])

    # Tablas
    prop_rows = "".join(
        [
            f"""<tr class="border-b border-slate-700/50 hover:bg-slate-750/30">
            <td class="py-2.5 px-3 text-emerald-400 font-mono font-medium">{p.get('rut')}-{p.get('dv')}</td>
            <td class="py-2.5 px-3 text-slate-200">{p.get('nombre')}</td>
            <td class="py-2.5 px-3 text-slate-400 text-sm">{p.get('fecharegistro', '')[:10]}</td>
        </tr>"""
            for p in propietarios
        ]
    )

    tasacion_rows = "".join(
        [
            f"""<tr class="border-b border-slate-700/50 hover:bg-slate-750/30">
            <td class="py-2.5 px-3 text-slate-200">{t.get('version')}</td>
            <td class="py-2.5 px-3 text-slate-300">{t.get('transmision')}</td>
            <td class="py-2.5 px-3 text-indigo-300 font-mono">${int(t.get('tasacion', 0)):,}</td>
            <td class="py-2.5 px-3 text-emerald-300 font-mono">${int(t.get('permiso', 0)):,}</td>
        </tr>"""
            for t in tasaciones
        ]
    )

    km_rows = "".join(
        [
            f"""<tr class="border-b border-slate-700/50 hover:bg-slate-750/30">
            <td class="py-2.5 px-3 text-slate-300">{km.get('fecharevision')}</td>
            <td class="py-2.5 px-3 text-amber-400 font-mono font-medium">{int(km.get('kilometraje', 0)):,} km</td>
            <td class="py-2.5 px-3 text-slate-400 text-sm">+{int(km.get('diferencia', 0)):,} km ({km.get('meses')} m)</td>
        </tr>"""
            for km in historial_km
        ]
    )

    return f"""<!DOCTYPE html>
<html lang="es" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Informe Vehicular - {ppu_text}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Oswald:wght@700&family=Roboto+Condensed:wght@700&display=swap" rel="stylesheet">
    <style>
        body {{ font-family: 'Inter', sans-serif; }}
        .plate-font {{ font-family: 'Roboto Condensed', 'Oswald', sans-serif; letter-spacing: 0.15em; }}
        .license-plate {{
            background: linear-gradient(180deg, #ffffff 0%, #f1f5f9 60%, #e2e8f0 100%);
            box-shadow: inset 0 0 0 2px #0f172a, inset 0 0 0 4px #cbd5e1, 0 4px 6px -1px rgba(0,0,0,0.5);
        }}
        .bolt {{
            background: radial-gradient(circle at 30% 30%, #ffffff 0%, #94a3b8 60%, #475569 100%);
            box-shadow: inset 0 1px 1px rgba(0,0,0,0.4), 0 1px 2px rgba(0,0,0,0.4);
        }}
        @media print {{
            .no-print {{ display: none !important; }}
            body {{ background-color: #ffffff !important; color: #000000 !important; }}
            .bg-slate-800, .bg-slate-900 {{ background-color: #ffffff !important; border-color: #e2e8f0 !important; color: #000000 !important; }}
            .text-white, .text-slate-200, .text-slate-300 {{ color: #0f172a !important; }}
        }}
    </style>
</head>
<body class="bg-slate-900 text-slate-100 min-h-screen p-4 md:p-8">
    <div class="max-w-5xl mx-auto space-y-6">
        
        <!-- Barra de navegación y búsqueda rápida -->
        <div class="no-print flex justify-between items-center bg-slate-800 border border-slate-700 rounded-2xl p-4">
            <a href="/" class="text-indigo-400 font-semibold hover:underline flex items-center gap-2">
                <span>←</span> Nueva Búsqueda
            </a>
            <button onclick="window.print()" class="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl text-sm font-semibold transition-all shadow-lg flex items-center gap-2">
                <span>🖨️</span> Descargar / Imprimir
            </button>
        </div>

        <!-- Alertas Críticas (Multas o Remates) -->
        {'<div class="bg-rose-500/10 border border-rose-500/30 text-rose-300 p-4 rounded-2xl flex items-center gap-3"><span>⚠️</span><strong>Vehículo Registra Remate:</strong> El vehículo posee antecedentes en casas de remate o pérdida total.</div>' if rematado else ''}
        {'<div class="bg-rose-500/10 border border-rose-500/30 text-rose-300 p-4 rounded-2xl flex items-center gap-3"><span>⚠️</span><strong>Multas Registradas:</strong> El vehículo presenta multas impagas activas.</div>' if multas else ''}

        <!-- Encabezado con Placa Patente -->
        <div class="bg-slate-800 border border-slate-700 rounded-2xl p-6 shadow-xl flex flex-col md:flex-row items-center justify-between gap-6">
            <div class="flex flex-col sm:flex-row items-center gap-6">
                <div class="relative license-plate w-56 h-24 rounded-xl border-4 border-slate-900 flex flex-col justify-between items-center py-1.5 px-3 select-none flex-shrink-0">
                    <div class="w-full flex justify-between px-2">
                        <div class="bolt w-2 h-2 rounded-full border border-slate-600"></div>
                        <div class="bolt w-2 h-2 rounded-full border border-slate-600"></div>
                    </div>
                    <div class="plate-font text-slate-900 text-[2.1rem] leading-none font-bold uppercase tracking-wider drop-shadow-[0_1px_1px_rgba(255,255,255,0.8)]">
                        {ppu_display}
                    </div>
                    <div class="w-full flex justify-center items-center relative">
                        <span class="text-[0.6rem] font-black tracking-[0.3em] text-slate-800 uppercase">CHILE</span>
                    </div>
                </div>

                <div class="text-center sm:text-left">
                    <h1 class="text-2xl font-bold text-white tracking-tight">{data.get('marca')} {data.get('modelo')}</h1>
                    <p class="text-slate-400 text-sm mt-1">Año {data.get('ano')} • {data.get('tipo')} • Color {detalle.get('color')}</p>
                </div>
            </div>

            <div class="flex gap-3">
                <span class="px-3.5 py-1.5 {'bg-rose-500/10 border-rose-500/30 text-rose-400' if rt_vencida else 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'} border rounded-xl text-sm font-semibold flex items-center gap-1.5">
                    <span class="w-2 h-2 rounded-full {'bg-rose-400' if rt_vencida else 'bg-emerald-400'} animate-pulse"></span>
                    RT: {'VENCIDA' if rt_vencida else revision.get('reultadocrt', 'AL DÍA')}
                </span>
                <span class="px-3.5 py-1.5 bg-amber-500/10 border border-amber-500/30 text-amber-400 rounded-xl text-sm font-semibold">
                    {detalle.get('kilometraje', '0')} KM
                </span>
            </div>
        </div>

        <!-- Ficha Técnica y Revisión Técnica -->
        <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div class="bg-slate-800 border border-slate-700 rounded-2xl p-6 shadow-lg">
                <h2 class="text-lg font-semibold text-white border-b border-slate-700 pb-3 mb-4 flex items-center gap-2">
                    <span>📋</span> Ficha Técnica
                </h2>
                <div class="grid grid-cols-2 gap-y-3 text-sm">
                    <div class="text-slate-400">N° Motor:</div>
                    <div class="text-slate-200 font-mono">{data.get('nromotor')}</div>
                    <div class="text-slate-400">N° Chasis / VIN:</div>
                    <div class="text-slate-200 font-mono">{data.get('nrochasis')}</div>
                    <div class="text-slate-400">Cilindrada:</div>
                    <div class="text-slate-200">{detalle.get('cilindrada')} cc</div>
                    <div class="text-slate-400">Combustible:</div>
                    <div class="text-slate-200">{detalle.get('combustible')}</div>
                    <div class="text-slate-400">Antigüedad:</div>
                    <div class="text-slate-200">{detalle.get('antiguedad')} años</div>
                </div>
            </div>

            <div class="bg-slate-800 border border-slate-700 rounded-2xl p-6 shadow-lg">
                <h2 class="text-lg font-semibold text-white border-b border-slate-700 pb-3 mb-4 flex items-center gap-2">
                    <span>🔍</span> Última Revisión Técnica
                </h2>
                <div class="grid grid-cols-2 gap-y-3 text-sm">
                    <div class="text-slate-400">Fecha Revisión:</div>
                    <div class="text-slate-200">{revision.get('fecharevision')}</div>
                    <div class="text-slate-400">Vencimiento:</div>
                    <div class="{'text-rose-400 font-semibold' if rt_vencida else 'text-emerald-400'}">{revision.get('fechavencimiento')}</div>
                    <div class="text-slate-400">Certificado N°:</div>
                    <div class="text-slate-200 font-mono text-xs">{revision.get('numcertificado')}</div>
                    <div class="text-slate-400">Planta / Comuna:</div>
                    <div class="text-slate-200">{revision.get('planta', {}).get('comuna')}</div>
                    <div class="text-slate-400">Estado:</div>
                    <div class="text-slate-200">{detalle.get('revisionsiguiente')}</div>
                </div>
            </div>
        </div>

        <!-- Gráfico e Historial de Kilometraje -->
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div class="bg-slate-800 border border-slate-700 rounded-2xl p-6 shadow-lg md:col-span-2">
                <h2 class="text-lg font-semibold text-white border-b border-slate-700 pb-3 mb-4 flex items-center gap-2">
                    <span>📊</span> Evolución del Kilometraje
                </h2>
                <div class="h-64">
                    <canvas id="kmChart"></canvas>
                </div>
            </div>

            <div class="bg-slate-800 border border-slate-700 rounded-2xl p-6 shadow-lg overflow-x-auto">
                <h2 class="text-lg font-semibold text-white border-b border-slate-700 pb-3 mb-4 flex items-center gap-2">
                    <span>📈</span> Registros PRT
                </h2>
                <table class="w-full text-left text-sm border-collapse">
                    <tbody>
                        {km_rows if km_rows else '<tr><td class="py-4 text-center text-slate-500">Sin registros</td></tr>'}
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Registro de Propietarios -->
        <div class="bg-slate-800 border border-slate-700 rounded-2xl p-6 shadow-lg">
            <h2 class="text-lg font-semibold text-white border-b border-slate-700 pb-3 mb-4 flex items-center gap-2">
                <span>👤</span> Registro de Propietarios
            </h2>
            <div class="overflow-x-auto">
                <table class="w-full text-left border-collapse">
                    <thead>
                        <tr class="text-xs text-slate-400 uppercase border-b border-slate-700">
                            <th class="py-2 px-3">RUT</th>
                            <th class="py-2 px-3">Nombre</th>
                            <th class="py-2 px-3">Fecha Registro</th>
                        </tr>
                    </thead>
                    <tbody>
                        {prop_rows if prop_rows else '<tr><td colspan="3" class="py-4 text-center text-slate-500">Sin registros</td></tr>'}
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Tasación Fiscal -->
        <div class="bg-slate-800 border border-slate-700 rounded-2xl p-6 shadow-lg">
            <h2 class="text-lg font-semibold text-white border-b border-slate-700 pb-3 mb-4 flex items-center gap-2">
                <span>🏷️</span> Tasación Fiscal y Permiso de Circulación
            </h2>
            <div class="overflow-x-auto">
                <table class="w-full text-left border-collapse">
                    <thead>
                        <tr class="text-xs text-slate-400 uppercase border-b border-slate-700">
                            <th class="py-2 px-3">Versión</th>
                            <th class="py-2 px-3">Transmisión</th>
                            <th class="py-2 px-3">Tasación</th>
                            <th class="py-2 px-3">Permiso Aprox.</th>
                        </tr>
                    </thead>
                    <tbody>
                        {tasacion_rows if tasacion_rows else '<tr><td colspan="4" class="py-4 text-center text-slate-500">Sin registros</td></tr>'}
                    </tbody>
                </table>
            </div>
        </div>

    </div>

    <!-- Script Chart.js -->
    <script>
        const ctx = document.getElementById('kmChart').getContext('2d');
        new Chart(ctx, {{
            type: 'line',
            data: {{
                labels: {chart_labels},
                datasets: [{{
                    label: 'Kilometraje Registrado',
                    data: {chart_data},
                    borderColor: '#f59e0b',
                    backgroundColor: 'rgba(245, 158, 11, 0.1)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.3,
                    pointBackgroundColor: '#f59e0b',
                    pointRadius: 4
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{ legend: {{ display: false }} }},
                scales: {{
                    x: {{ grid: {{ color: 'rgba(148, 163, 184, 0.1)' }}, ticks: {{ color: '#94a3b8' }} }},
                    y: {{ grid: {{ color: 'rgba(148, 163, 184, 0.1)' }}, ticks: {{ color: '#94a3b8' }} }}
                }}
            }}
        }});
    </script>
</body>
</html>"""


# Ruta principal con buscador interactivo
@app.get("/", response_class=HTMLResponse)
def home():
    return """<!DOCTYPE html>
<html lang="es" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Consulta Vehicular</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
    <style>body { font-family: 'Inter', sans-serif; }</style>
</head>
<body class="bg-slate-900 text-slate-100 min-h-screen flex items-center justify-center p-4">
    <div class="max-w-md w-full bg-slate-800 border border-slate-700 rounded-3xl p-8 shadow-2xl text-center space-y-6">
        <div class="space-y-2">
            <h1 class="text-3xl font-black tracking-tight text-white">Consulta Vehicular</h1>
            <p class="text-slate-400 text-sm">Ingresa la patente del vehículo para generar el informe</p>
        </div>
        <form onsubmit="event.preventDefault(); window.location.href='/ppu/' + document.getElementById('patente').value.toUpperCase().trim();" class="space-y-4">
            <input id="patente" type="text" placeholder="Ej: AABB12" required maxlength="6"
                class="w-full text-center uppercase tracking-widest text-2xl font-bold bg-slate-900 border border-slate-600 rounded-2xl p-4 text-white focus:outline-none focus:border-indigo-500 transition-all font-mono" />
            <button type="submit" class="w-full py-4 bg-indigo-600 hover:bg-indigo-500 font-semibold rounded-2xl text-white transition-all shadow-lg shadow-indigo-600/30">
                Consultar Informe
            </button>
        </form>
    </div>
</body>
</html>"""


# Endpoint unificado con soporte HTML y JSON (?format=json)
@app.get("/ppu/{patente}")
async def consultar_patente(patente: str, request: Request, format: Optional[str] = None):
    patente = patente.upper().strip()

    # 1. Consultar Caché
    if patente in cache:
        data = cache[patente]
    else:
        # 2. Petición Asíncrona al Servidor de Origen
        target_url = f"{BASE_URL}/{patente}"
        try:
            response = await http_client.get(target_url, headers=HEADERS)
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail="Error remoto")
            data = decrypt_hex_payload(response.text)
            cache[patente] = data
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error al procesar: {str(e)}")

    # 3. Retornar JSON si se solicita explícitamente o por Header
    if format == "json" or "application/json" in request.headers.get("accept", ""):
        return JSONResponse(content=data)

    # 4. Retornar vista Web enriquecida
    return HTMLResponse(content=generate_html_card(data))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
