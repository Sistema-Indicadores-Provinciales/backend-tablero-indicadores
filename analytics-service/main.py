import json
import math
import os
import traceback

import numpy as np
import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from app.generator import router, user, UPLOADS, MAX_BYTES
from app.data_engine import DataError, safe
from app.cors_origins import cors_origins
from pymongo.errors import PyMongoError
from pathlib import Path
from app.charts import prepare_chart_data
from app.excel_utils import get_excel_sheets, read_excel_data

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Excel Analytics Service",
    description="Microservicio para carga, lectura y análisis de archivos Excel.",
    version="1.3.0",
)

app.include_router(router)

@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    # Validation responses must never echo OAuth codes or client secrets.
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]} for error in exc.errors()
    ]})

@app.exception_handler(DataError)
async def data_error(request: Request, exc: DataError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})

@app.exception_handler(PyMongoError)
async def database_error(request: Request, exc: PyMongoError):
    return JSONResponse(status_code=503, content={"detail": "La base de datos no está disponible. Reintentá."})

@app.exception_handler(ValueError)
async def invalid_file(request: Request, exc: ValueError):
    return JSONResponse(status_code=422, content={"detail": "No se pudo interpretar el archivo. Revisá su formato y encabezados."})

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Carpetas
# ---------------------------------------------------------------------------

UPLOAD_DIR = str(Path(__file__).resolve().parent / "uploads")
DATA_DIR = str(Path(__file__).resolve().parent / "data")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def safe_value(val):
    return safe(val)


def serialize_records(df: pd.DataFrame) -> list[dict]:
    """Convierte un DataFrame a lista de dicts con valores seguros para JSON."""
    return [
        {col: safe_value(val) for col, val in row.items()}
        for row in df.to_dict(orient="records")
    ]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/upload/", dependencies=[Depends(user)])
async def upload_excel(file: UploadFile = File(...)):
    raise HTTPException(410, "Usá el nuevo generador para subir archivos de forma privada.")


@app.post("/read-columns/", dependencies=[Depends(user)])
async def read_columns(filename: str = Form(...), sheet_name: str = Form(...)):
    """
    Lee columnas, datos y metadatos de valores únicos por columna.
    El frontend usa column_meta para construir los filtros dinámicos.
    """
    try:
        file_path = str((Path(UPLOAD_DIR) / filename).resolve())
        if Path(file_path).parent != Path(UPLOAD_DIR).resolve():
            raise HTTPException(status_code=400, detail="Nombre de archivo no válido")

        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="El archivo no existe")

        df = read_excel_data(file_path, sheet_name)

        if df.empty:
            raise HTTPException(
                status_code=400,
                detail="La hoja seleccionada está vacía o no contiene datos",
            )

        # Metadatos por columna: tipo y valores únicos (para armar filtros en el frontend)
        column_meta: dict = {}
        for col in df.columns:
            unique_vals = df[col].dropna().unique().tolist()
            unique_vals = [safe_value(v) for v in unique_vals]
            try:
                unique_vals_sorted = sorted(unique_vals, key=lambda x: str(x))
            except Exception:
                unique_vals_sorted = unique_vals
            column_meta[col] = {
                "type": str(df[col].dtype),
                # Máx. 200 valores únicos para no saturar el frontend
                "unique_values": unique_vals_sorted[:200],
            }

        return JSONResponse(content={
            "columns": df.columns.tolist(),
            "column_meta": column_meta,
            "data": serialize_records(df),
        })

    except HTTPException:
        raise
    except Exception as e:
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error al leer columnas: {str(e)}")


@app.post("/generate-chart/", dependencies=[Depends(user)])
async def generate_chart(
    filename: str = Form(...),
    sheet_name: str = Form(...),
    x_col: str = Form(...),
    y_col: str = Form(...),
):
    try:
        file_path = str((Path(UPLOAD_DIR) / filename).resolve())
        if Path(file_path).parent != Path(UPLOAD_DIR).resolve():
            raise HTTPException(status_code=400, detail="Nombre de archivo no válido")

        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="El archivo no existe")

        df = read_excel_data(file_path, sheet_name)

        if df.empty:
            raise HTTPException(status_code=400, detail="La hoja seleccionada está vacía")

        if x_col not in df.columns or y_col not in df.columns:
            raise HTTPException(
                status_code=400,
                detail="Las columnas seleccionadas no existen en la hoja",
            )

        data = prepare_chart_data(df, x_col, y_col)
        return JSONResponse(content=data)

    except HTTPException:
        raise
    except Exception as e:
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error al generar datos del gráfico: {str(e)}")


@app.post("/filter-data/", dependencies=[Depends(user)])
async def filter_data(
    filename: str = Form(...),
    sheet_name: str = Form(...),
    x_col: str = Form(...),
    y_col: str = Form(...),
    group_col: str = Form(None),
    filters: str = Form("{}"),
):
    """
    Devuelve datos filtrados y agrupados listos para graficar con múltiples series.

    - filters: JSON string con { "COLUMNA": ["val1", "val2"] }
    - group_col: columna para segmentar en series (ej: CURSO, NIVEL, GESTION)
    """
    try:
        file_path = str((Path(UPLOAD_DIR) / filename).resolve())
        if Path(file_path).parent != Path(UPLOAD_DIR).resolve():
            raise HTTPException(status_code=400, detail="Nombre de archivo no válido")

        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="El archivo no existe")

        df = read_excel_data(file_path, sheet_name)

        if df.empty:
            raise HTTPException(status_code=400, detail="La hoja está vacía")

        # ── Aplicar filtros dinámicos ──
        try:
            filter_dict: dict = json.loads(filters)
        except Exception:
            filter_dict = {}

        for col, values in filter_dict.items():
            if col in df.columns and values:
                df = df[df[col].astype(str).isin([str(v) for v in values])]

        if df.empty:
            return JSONResponse(content={
                "labels": [],
                "datasets": [],
                "filtered_rows": 0,
            })

        # ── Validar columnas requeridas ──
        for col in [x_col, y_col]:
            if col not in df.columns:
                raise HTTPException(status_code=400, detail=f"Columna '{col}' no existe en la hoja")

        # ── Sin agrupación: serie única ──
        if not group_col or group_col not in df.columns:
            # Si y_col no es numérica, buscar primera columna numérica disponible
            if pd.api.types.is_numeric_dtype(df[y_col]):
                value_col = y_col
            else:
                numeric_cols = [
                    c for c in df.columns
                    if pd.api.types.is_numeric_dtype(df[c]) and c != x_col
                ]
                if not numeric_cols:
                    raise HTTPException(
                        status_code=400,
                        detail=f"La columna '{y_col}' no es numérica y no se encontró otra columna numérica."
                    )
                value_col = numeric_cols[0]

            df["__y__"] = pd.to_numeric(df[value_col], errors="coerce")
            grouped = df.groupby(x_col, sort=True)["__y__"].mean().reset_index()
            try:
                grouped = grouped.sort_values(x_col)
            except Exception:
                pass

            return JSONResponse(content={
                "labels": [safe_value(v) for v in grouped[x_col].tolist()],
                "datasets": [{
                    "label": value_col,
                    "data": [None if (v != v) else safe_value(v) for v in grouped["__y__"].tolist()],
                }],
                "filtered_rows": len(df),
            })

        # ── Con agrupación: pivot_table genera la matriz completa sin NaN por tipo ──

        # 1. Determinar qué columna contiene los valores numéricos a graficar.
        #    Si y_col es numérica, usarla directamente.
        #    Si es texto (ej: columna "TASA" con strings), buscar la primera
        #    columna numérica del df que no sea x_col ni group_col.
        if pd.api.types.is_numeric_dtype(df[y_col]):
            value_col = y_col
        else:
            numeric_cols = [
                c for c in df.columns
                if pd.api.types.is_numeric_dtype(df[c])
                and c not in (x_col, group_col)
            ]
            if not numeric_cols:
                raise HTTPException(
                    status_code=400,
                    detail=f"La columna '{y_col}' no es numérica y no se encontró otra columna numérica para graficar."
                )
            value_col = numeric_cols[0]

        # 2. Normalizar x y group a string para evitar mismatch int/float/str
        df["__x__"] = df[x_col].astype(str)
        df["__g__"] = df[group_col].astype(str)
        df["__y__"] = pd.to_numeric(df[value_col], errors="coerce")

        # 3. Elegir aggfunc: si hay exactamente 1 fila por combinación → mean == first
        #    Para tasas, mean es siempre más correcto que sum
        pivot = df.pivot_table(
            index="__x__",
            columns="__g__",
            values="__y__",
            aggfunc="mean",
        )

        # 4. Ordenar eje X
        try:
            pivot = pivot.reindex(sorted(pivot.index.tolist(), key=lambda v: str(v)))
        except Exception:
            pass

        labels = pivot.index.tolist()
        group_values = pivot.columns.tolist()

        datasets = []
        for gval in group_values:
            col_data = pivot[gval].tolist()
            datasets.append({
                "label": str(gval),
                # NaN de pandas → None para JSON (v != v es True solo para NaN)
                "data": [None if (v != v) else safe_value(v) for v in col_data],
            })

        # 5. Limpiar columnas temporales
        df.drop(columns=["__x__", "__g__", "__y__"], inplace=True, errors="ignore")

        return JSONResponse(content={
            "labels": labels,
            "datasets": datasets,
            "filtered_rows": len(df),
        })

    except HTTPException:
        raise
    except Exception as e:
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error al filtrar datos: {str(e)}")


@app.get("/health/")
async def health_check():
    return {"status": "ok", "message": "Servicio FastAPI activo y funcionando."}

@app.post("/system-data/read-columns/", dependencies=[Depends(user)])
async def system_read_columns(filename: str = Form(...), sheet_name: str = Form(...)):
    try:
        # Busca el archivo en data/ recursivamente
        file_path = None
        for root, dirs, files in os.walk(DATA_DIR):
            if filename in files:
                file_path = os.path.join(root, filename)
                break

        if not file_path:
            raise HTTPException(status_code=404, detail=f"Archivo '{filename}' no encontrado en data/")

        df = read_excel_data(file_path, sheet_name)

        if df.empty:
            raise HTTPException(status_code=400, detail="La hoja está vacía o no tiene datos válidos")

        column_meta: dict = {}
        for col in df.columns:
            unique_vals = df[col].dropna().unique().tolist()
            unique_vals = [safe_value(v) for v in unique_vals]
            try:
                unique_vals_sorted = sorted(unique_vals, key=lambda x: str(x))
            except Exception:
                unique_vals_sorted = unique_vals
            column_meta[col] = {
                "type": str(df[col].dtype),
                "unique_values": unique_vals_sorted[:200],
            }

        return JSONResponse(content={
            "columns": df.columns.tolist(),
            "column_meta": column_meta,
            "data": serialize_records(df),
        })

    except HTTPException:
        raise
    except Exception as e:
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error al leer archivo del sistema: {str(e)}")
    
@app.get("/get-test")
async def get_test():
    print("Hola desde el endpoint /get-test")
    
    return {
        "message": "Console log ejecutado correctamente"
    }
