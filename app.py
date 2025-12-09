from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import re, os, requests
from io import BytesIO
from pdfminer.high_level import extract_text

app = FastAPI()

# --- Permitir llamadas desde GPT y navegadores ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OUTPUT_FILE = "convocatorias.xlsx"

# -------------------------------
# 🔹 Funciones auxiliares
# -------------------------------
def format_currency(v):
    if not v:
        return ""
    s = re.sub(r"[^0-9.,]", "", v)
    s = s.replace(",", ".")
    try:
        n = float(s)
        return f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return ""

def format_date(v):
    if not v:
        return ""
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", v)
    if not m:
        return ""
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

def extract_field(b, label, next_labels):
    if label not in b:
        return ""
    s = b.index(label) + len(label)
    e = len(b)
    for nl in next_labels:
        p = b.find(nl, s)
        if p != -1 and p < e:
            e = p
    value = b[s:e].strip(" \n\r\t:")
    value = re.sub(r"\s+", " ", value)
    return value

ALL_LABELS = [
    "Tipo de publicación:", "Ámbito:", "Entidad Adjudicadora:", "Datos de contacto:",
    "Objeto:", "Tramitacion y Procedimiento:", "Tramitación y Procedimiento:",
    "Expediente:", "Presupuesto:", "Valor estimado del contrato:",
    "Enlace al pliego:", "Vencimiento:"
]

# -------------------------------
# 🔹 Endpoint principal
# -------------------------------
@app.post("/procesar-licitaciones")
async def procesar_licitaciones(req: Request):
    data = await req.json()
    file_urls = data.get("fileUrls", [])
    rows = []

    for url in file_urls:
        try:
            print(f"Descargando PDF: {url}")
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            pdf_data = BytesIO(r.content)
            text = extract_text(pdf_data)
        except Exception as e:
            print(f"❌ Error al procesar {url}: {e}")
            continue

        # Dividir por posibles encabezados
        blocks = re.split(r"(?=Número de pliego:|Nº de expediente:|Convocatoria de licitación)", text)
        blocks = [b for b in blocks if any(x in b for x in ["CONVOCATORIA", "Convocatoria", "licitación"])]

        for block in blocks:
            tipo_match = re.search(r"(CONVOCATORIA|Convocatoria|Licitación)", block)
            tipo = tipo_match.group(1).strip() if tipo_match else ""
            if not tipo:
                continue

            rows.append({
                "Ámbito": extract_field(block, "Ámbito:", ALL_LABELS),
                "Entidad Adjudicadora": extract_field(block, "Entidad Adjudicadora:", ALL_LABELS),
                "Objeto": extract_field(block, "Objeto:", ALL_LABELS),
                "Tramitacion y Procedimiento": extract_field(block, "Tramitacion y Procedimiento:", ALL_LABELS)
                    or extract_field(block, "Tramitación y Procedimiento:", ALL_LABELS),
                "Expediente": extract_field(block, "Expediente:", ALL_LABELS),
                "Presupuesto": format_currency(extract_field(block, "Presupuesto:", ALL_LABELS)),
                "Valor estimado del contrato": format_currency(extract_field(block, "Valor estimado del contrato:", ALL_LABELS)),
                "Enlace al pliego (URL)": extract_field(block, "Enlace al pliego:", ALL_LABELS),
                "Vencimiento": format_date(extract_field(block, "Vencimiento:", ALL_LABELS)),
            })

    # Guardar Excel en /tmp
    output_path = f"/tmp/{OUTPUT_FILE}"
    df = pd.DataFrame(rows)
    df.to_excel(output_path, index=False)

    # Enlace público
    public_url = f"https://procesador-licitaciones.onrender.com/descargar/{OUTPUT_FILE}"
    return JSONResponse({"excelUrl": public_url})

# -------------------------------
# 🔹 Endpoint para descargar Excel
# -------------------------------
@app.get("/descargar/{filename}")
async def descargar_archivo(filename: str):
    file_path = f"/tmp/{filename}"
    if not os.path.exists(file_path):
        return JSONResponse({"error": "Archivo no encontrado"}, status_code=404)
    return FileResponse(file_path, filename=filename)

# -------------------------------
# 🔹 Health check
# -------------------------------
@app.get("/")
async def root():
    return {"status": "ok"}

