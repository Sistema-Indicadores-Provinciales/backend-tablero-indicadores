"""Bounded tabular import and explicit chart semantics, independent of HTTP/storage."""
import csv
import math
import re
from datetime import date, datetime, time
from pathlib import Path
from zipfile import ZipFile, BadZipFile

import numpy as np
import pandas as pd
from openpyxl import load_workbook

MAX_ROWS = 100_000
MAX_COLS = 256
MAX_CELLS = 2_000_000
MAX_POINTS = 10_000
CHART_TYPES = {"bar", "horizontal", "stacked", "line", "area", "scatter", "pie", "donut", "histogram", "box", "heatmap", "table", "indicator"}

class DataError(ValueError):
    pass

def safe(value):
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, (datetime, date, time, pd.Timestamp)):
        return value.isoformat()
    return str(value)

def bounded(rows):
    result = []
    cells = 0
    for row in rows:
        if len(result) >= MAX_ROWS or len(row) > MAX_COLS:
            raise DataError("La hoja supera el límite de 100.000 filas o 256 columnas. Dividila en hojas más pequeñas.")
        cells += len(row)
        if cells > MAX_CELLS:
            raise DataError("La hoja supera los 2 millones de celdas. Reducí el rango de datos.")
        result.append([safe(v) for v in row])
    return result

def inspect_zip(path):
    try:
        with ZipFile(path) as z:
            if sum(i.file_size for i in z.infolist()) > 100 * 1024 * 1024:
                raise DataError("El Excel descomprimido supera 100 MB.")
    except BadZipFile as exc:
        raise DataError("El archivo no es un Excel válido o está protegido con contraseña.") from exc

def read_rows(path, sheet=None):
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        inspect_zip(path)
        book = load_workbook(path, read_only=True, data_only=True)
        try:
            names = book.sheetnames
            if sheet is None:
                return names
            if sheet not in names:
                raise DataError("La hoja seleccionada no existe.")
            ws = book[sheet]
            if ws.max_row > MAX_ROWS or ws.max_column > MAX_COLS:
                raise DataError("La hoja supera los límites de filas o columnas.")
            return bounded(ws.iter_rows(values_only=True))
        finally:
            book.close()
    if suffix == ".xls":
        import xlrd
        book = xlrd.open_workbook(path, on_demand=True)
        try:
            if sheet is None:
                return book.sheet_names()
            ws = book.sheet_by_name(sheet)
            if ws.nrows > MAX_ROWS or ws.ncols > MAX_COLS or ws.nrows * ws.ncols > MAX_CELLS:
                raise DataError("La hoja supera los límites de lectura.")
            def rows():
                for i in range(ws.nrows):
                    yield [xlrd.xldate_as_datetime(c.value, book.datemode) if c.ctype == xlrd.XL_CELL_DATE else None if c.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_ERROR) else c.value for c in ws.row(i)]
            return bounded(rows())
        finally:
            book.release_resources()
    if suffix in {".csv", ".tsv"}:
        if sheet is None:
            return ["Datos"]
        if sheet != "Datos":
            raise DataError("La hoja seleccionada no existe.")
        for encoding in ("utf-8-sig", "cp1252"):
            try:
                with path.open(encoding=encoding, newline="") as stream:
                    sample = stream.read(8192)
                    stream.seek(0)
                    try:
                        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                    except csv.Error:
                        dialect = csv.excel_tab if suffix == ".tsv" else csv.excel
                    return bounded(csv.reader(stream, dialect))
            except UnicodeDecodeError:
                continue
        raise DataError("No se pudo leer la codificación del CSV. Guardalo como UTF-8.")
    raise DataError("Formato admitido: XLSX, XLSM, XLS, CSV o TSV.")

def number(value, decimal=","):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return safe(float(value))
    text = str(value).strip().replace("\u00a0", "").replace(" ", "")
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    percent = text.endswith("%")
    text = text.rstrip("%")
    text = re.sub(r"^(?:ARS|USD|EUR|[$€£])", "", text, flags=re.I)
    thousands = "." if decimal == "," else ","
    # A thousands separator must group exactly three digits. Do not turn 1.5 into 15.
    integer = text.split(decimal)[0].lstrip("+-")
    if thousands in text:
        if not re.fullmatch(r"\d{1,3}(?:" + re.escape(thousands) + r"\d{3})+", integer):
            return None
        if decimal in text and thousands in text.split(decimal, 1)[1]:
            return None
    text = text.replace(thousands, "").replace(decimal, ".")
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", text):
        return None
    result = float(text) * (-1 if negative else 1) / (100 if percent else 1)
    return result if math.isfinite(result) else None

def table(rows, header=1, decimal=",", types=None):
    if not rows:
        raise DataError("La hoja está vacía.")
    if header < 0 or header > len(rows):
        raise DataError("La fila de encabezados no existe. Usá 0 si no hay encabezados.")
    width = max(map(len, rows))
    raw_names = rows[header - 1] if header else []
    names, used = [], set()
    for i in range(width):
        name = str(raw_names[i]).strip() if i < len(raw_names) and raw_names[i] is not None else ""
        base = name or f"Columna {i + 1}"
        name, n = base, 2
        while name in used:
            name, n = f"{base} ({n})", n + 1
        names.append(name)
        used.add(name)
    records = [list(r) + [None] * (width - len(r)) for r in rows[header:]]
    records = [r for r in records if any(v is not None and v != "" for v in r)]
    if not records:
        raise DataError("No hay datos debajo de los encabezados. Revisá la fila elegida.")
    frame = pd.DataFrame(records, columns=names, dtype=object)
    warnings = []
    meta = {}
    types = types or {}
    if set(types) - set(names):
        raise DataError("La configuración de tipos contiene columnas inexistentes.")
    for col in names:
        values = frame[col].tolist()
        present = [v for v in values if v is not None and v != ""]
        kind = types.get(col, "auto")
        if kind == "auto":
            numeric = present and all(number(v, decimal) is not None for v in present)
            ids = any(isinstance(v, str) and re.match(r"^0\d+$", v) for v in present)
            kind = "boolean" if present and all(isinstance(v, bool) for v in present) else "number" if numeric and not ids else "text"
            if kind == "text" and present and all(re.match(r"^\d{4}-\d{2}-\d{2}(?:T.*)?$", str(v)) for v in present):
                kind = "date"
        if kind == "number":
            converted = [number(v, decimal) for v in values]
        elif kind == "date":
            def parse_date(v):
                if v is None or v == "":
                    return None
                try:
                    text = str(v)
                    if re.match(r"^\d{4}-\d{2}-\d{2}", text):
                        return pd.to_datetime(text, format="ISO8601").isoformat()
                    return pd.to_datetime(text, dayfirst=decimal == ",").isoformat()
                except (ValueError, TypeError, OverflowError):
                    return None
            converted = [parse_date(v) for v in values]
        elif kind == "boolean":
            mapping = {"true": True, "false": False, "si": True, "sí": True, "no": False, "1": True, "0": False}
            converted = [mapping.get(str(v).strip().lower()) if v is not None else None for v in values]
        elif kind == "text":
            converted = [str(v) if v is not None else None for v in values]
        else:
            raise DataError("Tipo de columna no válido.")
        invalid = sum(v is not None and v != "" and c is None for v, c in zip(values, converted))
        if invalid:
            warnings.append(f"{col}: {invalid} valores no pudieron convertirse a {kind}; se tratarán como vacíos.")
        frame[col] = pd.Series(converted, dtype=object)
        unique = list(dict.fromkeys(v for v in converted if v is not None))
        meta[col] = {"type": kind, "unique_values": unique[:100], "unique_count": len(unique), "missing": sum(v is None for v in converted), "invalid": invalid}
    return frame, {"columns": names, "column_meta": meta, "row_count": len(frame), "preview": [{c: safe(v) for c, v in row.items()} for row in frame.head(20).to_dict("records")], "warnings": warnings}

def filter_value(value):
    value = safe(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def filter_frame(frame, filters):
    for col, values in filters.items():
        if col not in frame or not isinstance(values, list):
            raise DataError("Filtro no válido.")
        if values:
            sample = next((safe(v) for v in frame[col] if safe(v) is not None), None)
            selected = [str(v) for v in values]
            if isinstance(sample, (int, float)) and not isinstance(sample, bool):
                def numeric_key(value):
                    try:
                        parsed = float(value)
                        return filter_value(parsed) if math.isfinite(parsed) else value
                    except (ValueError, TypeError, OverflowError):
                        return value
                selected = [numeric_key(v) for v in selected]
            elif isinstance(sample, bool):
                selected = [{'true': 'True', 'false': 'False'}.get(v.lower(), v) for v in selected]
            frame = frame[frame[col].map(filter_value).isin(selected)]
    return frame


def chart(frame, cfg):
    kind = cfg.get("chart_type", "bar")
    agg = cfg.get("aggregation", "sum")
    x, y, group = cfg.get("x_col", ""), cfg.get("y_col", ""), cfg.get("group_col", "")
    if kind not in CHART_TYPES or agg not in {"sum", "mean", "median", "min", "max", "count", "distinct"}:
        raise DataError("Tipo de gráfico u operación no válido.")
    required = [x] + ([y] if agg != "count" or kind in {"scatter", "box", "histogram"} else []) + ([group] if group else [])
    if any(c not in frame for c in required):
        raise DataError("Elegí columnas válidas para este gráfico.")
    frame = frame.copy()
    frame = filter_frame(frame, cfg.get("filters", {}))
    rows = len(frame)
    warnings = []
    if kind == "table":
        cols = list(frame.columns)
        return {"labels": [], "datasets": [], "filtered_rows": rows, "columns": cols, "records": [{c: safe(v) for c, v in r.items()} for r in frame[cols].head(500).to_dict("records")], "warnings": ["Vista limitada a 500 filas."] if rows > 500 else []}
    if not rows:
        return {"labels": [], "datasets": [], "filtered_rows": 0, "warnings": []}
    numeric_required = agg not in {"count", "distinct"} or kind in {"scatter", "histogram", "box"}
    if numeric_required:
        if any(v is not None and not isinstance(v, (int, float, bool)) for v in frame[y]):
            raise DataError(f"'{y}' contiene texto. Elegí contar registros o configurá su tipo como número en la vista previa.")
        missing = frame[y].isna().sum()
        if missing:
            warnings.append(f"Se omitieron {missing} filas sin valor numérico en {y}.")
        frame = frame[frame[y].notna()]
        if frame.empty:
            raise DataError("No hay valores numéricos válidos para esta selección.")
    if cfg.get("date_bucket", "none") != "none":
        if cfg["date_bucket"] not in {"month", "year"}:
            raise DataError("Agrupación temporal no válida.")
        dates = pd.to_datetime(frame[x], errors="coerce")
        if dates.isna().any():
            raise DataError("Para agrupar por fecha, configurá el eje X como fecha y corregí los valores vacíos.")
        frame[x] = dates.dt.strftime("%Y-%m" if cfg["date_bucket"] == "month" else "%Y")
    if kind in {"scatter", "histogram", "box"}:
        if len(frame) > MAX_POINTS:
            raise DataError("El gráfico supera 10.000 puntos. Aplicá filtros antes de generar.")
        if kind == "scatter" and any(v is None or not isinstance(v, (int, float)) for v in frame[x]):
            raise DataError("La dispersión necesita un eje X numérico sin vacíos.")
        datasets = []
        grouping = group or (x if kind == "box" else None)
        groups = frame.groupby(grouping, dropna=False, sort=False) if grouping else [(y, frame)]
        for label, sub in groups:
            datasets.append({"label": str(safe(label) or "Sin dato"), "x": [safe(v) for v in sub[x]], "data": [safe(v) for v in sub[y]]})
        if len(datasets) > 50:
            raise DataError("Hay más de 50 series. Aplicá filtros.")
        return {"labels": [], "datasets": datasets, "filtered_rows": rows, "warnings": warnings}
    keys = list(dict.fromkeys([x] + ([group] if group else [])))
    grouped = frame.groupby(keys, dropna=False, sort=False)
    if agg == "count":
        result = grouped.size()
    elif agg == "distinct":
        result = grouped[y].nunique()
    elif agg == "sum":
        result = grouped[y].sum(min_count=1)
    else:
        result = getattr(grouped[y], agg)()
    if len(result) > MAX_POINTS:
        raise DataError("El resultado supera 10.000 puntos. Aplicá filtros o agrupá por mes/año.")
    pairs = [(k if isinstance(k, tuple) else (k,), safe(v)) for k, v in result.items()]
    labels = list(dict.fromkeys(safe(k[0]) for k, _ in pairs))
    labels.sort(key=lambda v: (v is None, 0 if isinstance(v, (int, float)) else 1, v if isinstance(v, (int, float)) else str(v)))
    group_index = keys.index(group) if group else None
    series = list(dict.fromkeys(str(safe(k[group_index])) for k, _ in pairs)) if group else ["Registros" if agg == "count" else y]
    if len(series) > 50:
        raise DataError("Hay más de 50 series. Aplicá filtros.")
    matrix = {(safe(k[0]), str(safe(k[group_index])) if group else series[0]): v for k, v in pairs}
    datasets = [{"label": s, "data": [matrix.get((v, s)) for v in labels]} for s in series]
    if kind in {"pie", "donut"}:
        if len(datasets) > 1 or len(labels) > 30:
            raise DataError("Circular/anillo admite una serie y hasta 30 categorías. Quitá la agrupación o aplicá filtros.")
        values = datasets[0]["data"]
        if any(v is not None and v < 0 for v in values) or not any(v and v > 0 for v in values):
            raise DataError("Circular/anillo necesita valores no negativos y al menos uno positivo.")
    if kind == "indicator":
        if agg == "count":
            value = rows
        elif agg == "distinct":
            value = int(frame[y].nunique())
        elif agg == "sum":
            value = safe(frame[y].sum(min_count=1))
        else:
            value = safe(getattr(frame[y], agg)())
        return {"labels": ["Total"], "datasets": [{"label": agg, "data": [value]}], "filtered_rows": rows, "warnings": warnings}
    return {"labels": labels, "datasets": datasets, "filtered_rows": rows, "warnings": warnings}
