from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import pandas as pd
import re, os
from PyPDF2 import PdfReader

app = FastAPI()

def format_currency(v):
    if not v:
        return ""
    s = re.sub(r"[^0-9.]", "", v)
    if not s:
        return ""
    if "." in s:
        parts = s.split(".")
        intp = "".join(parts[:-1]) or parts[0]
        dec = parts[-1][:2].ljust(2, "0")
    else:
        intp, dec = s, "00"
    r = intp[::-1]
    groups = [r[i:i+3] for i in range(0, len(r), 3)]
    intf = ".".join(g[::-1] for g in groups[::-1])
    return f"{intf},{dec}"

def format_date(v):
    if not v:
        return ""
    if "PENDIENTE" in v.upper():
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

@app.post("/procesar-licitaciones")
async def procesar_licitaciones(req: Request):
    data = await req.json()
    file_paths = data.get("fileUrls", [])
    rows = []

    for path in file_paths:
        if not os.path.exists(path):
            continue
        reader = PdfReader(path)
        for page in reader.pages:
            page_text = page.extract_text() or ""
            page_uris = []
            annots = page.get("/Annots")
            if annots:
                for a in annots:
                    try:
                        obj = a.get_object()
                    except Exception:
                        continue
                    action = obj.get("/A")
                    if action:
                        try:
                            action_obj = action.get_object()
                        except Exception:
                            action_obj = action
                        uri = action_obj.get("/URI") if hasattr(action_obj, "get") else None
                        if isinstance(uri, str) and uri.startswith(("http://", "https://")):
                            page_uris.append(uri)

            blocks = [b for b in re.split(r"(?=Número de pliego:)", page_text) if "Número de pliego:" in b]
            uri_index_page = 0

            for block in blocks:
                tipo_match = re.search(r"Número de pliego:.*\n([A-ZÁÉÍÓÚÑÜ ]+)", block)
                tipo = tipo_match.group(1).strip() if tipo_match else ""
                if tipo != "CONVOCATORIA":
                    continue

                url = ""
                if "Enlace al pliego:" in block and uri_index_page < len(page_uris):
                    url = page_uris[uri_index_page]
                    uri_index_page += 1

                rows.append({
                    "Ámbito": extract_field(block, "Ámbito:", ALL_LABELS),
                    "Entidad Adjudicadora": extract_field(block, "Entidad Adjudicadora:", ALL_LABELS),
                    "Objeto": extract_field(block, "Objeto:", ALL_LABELS),
                    "Tramitacion y Procedimiento": extract_field(block, "Tramitacion y Procedimiento:", ALL_LABELS)
                        or extract_field(block, "Tramitación y Procedimiento:", ALL_LABELS),
                    "Expediente": extract_field(block, "Expediente:", ALL_LABELS),
                    "Presupuesto": format_currency(extract_field(block, "Presupuesto:", ALL_LABELS)),
                    "Valor estimado del contrato": format_currency(extract_field(block, "Valor estimado del contrato:", ALL_LABELS)),
                    "Enlace al pliego (URL)": url,
                    "Vencimiento": format_date(extract_field(block, "Vencimiento:", ALL_LABELS)),
                })

    df = pd.DataFrame(rows)
    output_path = "/tmp/convocatorias.xlsx"
    df.to_excel(output_path, index=False)
    return JSONResponse({"excelUrl": output_path})
