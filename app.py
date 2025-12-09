from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd, re, os, requests, unicodedata
from io import BytesIO
from pdfminer.high_level import extract_text
import PyPDF2

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OUTPUT_FILE = "convocatorias.xlsx"

# -----------------------------------------
# 🔹 Utilidades
# -----------------------------------------
def limpiar_texto(texto: str) -> str:
    """ETAPA 1 – Preprocesamiento: limpia y normaliza texto PDF."""
    # Normalizar codificación Unicode
    texto = unicodedata.normalize("NFKC", texto)
    texto = texto.replace("\xa0", " ").replace("\t", " ").replace("\r", " ")
    texto = re.sub(r"[^\S\n]+", " ", texto)  # eliminar dobles espacios

    # Mantener saltos antes de etiquetas conocidas
    etiquetas = [
        "Ámbito:", "Entidad Adjudicadora:", "Objeto:", "Tramitacion y Procedimiento:",
        "Tramitación y Procedimiento:", "Expediente:", "Presupuesto:",
        "Valor estimado del contrato:", "Enlace al pliego:", "Vencimiento:"
    ]
    for e in etiquetas:
        texto = texto.replace(e, f"\n{e}")

    # Eliminar saltos innecesarios dentro de frases
    texto = re.sub(r"(?<!:)\n(?!\w+:)", " ", texto)

    # Normalizar tildes y mayúsculas/minúsculas
    texto = texto.replace("Ã¡", "á").replace("Ã©", "é").replace("Ã­", "í")
    texto = texto.replace("Ã³", "ó").replace("Ãº", "ú").replace("Ã±", "ñ")

    return texto.strip()

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

# -----------------------------------------
# 🔹 Endpoint principal
# -----------------------------------------
@app.post("/procesar-licitaciones")
async def procesar_licitaciones(req: Request):
    data = await req.json()
    file_urls = data.get("fileUrls", [])
    strict_mode = data.get("strictMode", True)
    rows = []

    for url in file_urls:
        try:
            print(f"📥 Descargando PDF: {url}")
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            pdf_data = BytesIO(r.content)

            # Extraer texto crudo página a página
            reader = PyPDF2.PdfReader(pdf_data)
            paginas_limpias = []
            for i, page in enumerate(reader.pages):
                texto_crudo = extract_text(BytesIO(r.content), page_numbers=[i])
                paginas_limpias.append(limpiar_texto(texto_crudo or ""))

            texto_total = "\n".join(paginas_limpias)

            # Extraer URLs reales
            pdf_data.seek(0)
            urls_encontradas = []
            for page in reader.pages:
                annots = page.get("/Annots")
                if not annots:
                    continue
                for a in annots:
                    try:
                        obj = a.get_object()
                        uri = None
                        if "/A" in obj:
                            action = obj.get("/A")
                            if action:
                                uri = action.get("/URI")
                        if not uri and "/URI" in obj:
                            uri = obj.get("/URI")
                        if not uri and "/Action" in obj:
                            act = obj.get("/Action")
                            if isinstance(act, dict) and "/URI" in act:
                                uri = act["/URI"]
                        if isinstance(uri, str) and uri.startswith(("http://", "https://")):
                            urls_encontradas.append(uri)
                    except Exception:
                        continue
            print(f"🔗 URLs detectadas: {len(urls_encontradas)}")

        except Exception as e:
            print(f"❌ Error al procesar {url}: {e}")
            continue

        # -----------------------------------------
        # ETAPA 2 – Extracción de convocatorias
        # -----------------------------------------
        blocks = re.split(r"(?=CONVOCATORIA)", texto_total, flags=re.IGNORECASE)
        blocks = [b for b in blocks if "CONVOCATORIA" in b]

        url_index = 0
        for block in blocks:
            enlace = ""
            if url_index < len(urls_encontradas):
                enlace = urls_encontradas[url_index]
                url_index += 1

            if strict_mode and not enlace:
                continue

            rows.append({
                "Ámbito": extract_field(block, "Ámbito:", []),
                "Entidad Adjudicadora": extract_field(block, "Entidad Adjudicadora:", []),
                "Objeto": extract_field(block, "Objeto:", []),
                "Tramitacion y Procedimiento": extract_field(block, "Tramitacion y Procedimiento:", [])
                    or extract_field(block, "Tramitación y Procedimiento:", []),
                "Expediente": extract_field(block, "Expediente:", []),
                "Presupuesto": format_currency(extract_field(block, "Presupuesto:", [])),
                "Valor estimado del contrato": format_currency(extract_field(block, "Valor estimado del contrato:", [])),
                "Enlace al pliego (URL)": enlace,
                "Vencimiento": format_date(extract_field(block, "Vencimiento:", [])),
            })

    # Crear Excel final
    output_path = f"/tmp/{OUTPUT_FILE}"
    df = pd.DataFrame(rows)
    df.to_excel(output_path, index=False)

    public_url = f"https://procesador-licitaciones.onrender.com/descargar/{OUTPUT_FILE}"
    return JSONResponse({
        "excelUrl": public_url,
        "registros": len(rows),
        "strictMode": strict_mode
    })

# -----------------------------------------
# 🔹 Descarga y Health check
# -----------------------------------------
@app.get("/descargar/{filename}")
async def descargar_archivo(filename: str):
    file_path = f"/tmp/{filename}"
    if not os.path.exists(file_path):
        return JSONResponse({"error": "Archivo no encontrado"}, status_code=404)
    return FileResponse(file_path, filename=filename)

@app.get("/")
async def root():
    return {"status": "ok"}

@app.head("/")
async def head_root():
    return {"status": "ok"}
