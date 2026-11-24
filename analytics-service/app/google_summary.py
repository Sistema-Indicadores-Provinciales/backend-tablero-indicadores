"""Read a complete large worksheet in bounded blocks, retaining only aggregates."""
from time import monotonic

from .data_engine import DataError, MAX_CELLS, number, table

MAX_SOURCE_ROWS = 1_000_000
MAX_SOURCE_CELLS = 10_000_000
MAX_GROUPS = 150_000
MAX_GROUP_BYTES = 192 * 1024 * 1024
MAX_SECONDS = 65
BLOCK_ROWS = 50_000


def summarize_google(source, config, token, read):
    """COUNT(nonempty column), COUNT(*) and MAX, grouped by explicit dimensions.

    No row limit is used as a truncation point. A failed block/limit aborts the
    entire read. Patient/identifier columns used only for COUNT never survive
    the current block or enter the returned preview/snapshot.
    """
    started = monotonic()
    spec = config.google_summary
    info = read(source, token)
    props = next((s['properties'] for s in info.get('sheets', [])
                  if s['properties']['title'] == config.sheet), None)
    if not props:
        raise DataError('La pestaña seleccionada no existe.')
    grid = props.get('gridProperties', {})
    rows, cols = grid.get('rowCount', 0), grid.get('columnCount', 0)
    if not (1 <= rows <= MAX_SOURCE_ROWS and 1 <= cols <= 256):
        raise DataError('La hoja supera los límites de lectura completa por bloques.')
    if not 1 <= config.header_row < rows:
        raise DataError('Elegí una fila de encabezados con datos debajo.')
    headers = read(source, token, config.sheet, row_range=(config.header_row, config.header_row, cols)).get('values', [])
    names = [str(v).strip() if v is not None else '' for v in headers[0]] if headers else []
    required = set(spec.dimensions) | set(spec.counts.values()) | set(spec.max_values.values())
    if any(names.count(name) != 1 for name in required):
        raise DataError('El resumen necesita columnas existentes con encabezados únicos.')
    outputs = [*spec.dimensions, *spec.counts, *spec.max_values, spec.row_count]
    if len(outputs) != len(set(outputs)):
        raise DataError('Los nombres de las columnas del resumen no pueden repetirse.')
    if set(config.types) - set(outputs):
        raise DataError('Los tipos del resumen deben corresponder a sus columnas de salida.')
    # COUNT(*) also includes rows populated only in a non-selected column.
    # Read the full declared width, then discard fields outside the summary.
    width = cols
    if rows * width > MAX_SOURCE_CELLS:
        raise DataError('La hoja supera los 10 millones de celdas de lectura por bloques.')
    dimensions = [names.index(name) for name in spec.dimensions]
    counts = [names.index(name) for name in spec.counts.values()]
    maxima = [names.index(name) for name in spec.max_values.values()]
    groups, source_rows, group_bytes = {}, 0, 0
    for start in range(config.header_row + 1, rows + 1, BLOCK_ROWS):
        if monotonic() - started > MAX_SECONDS:
            raise DataError('La lectura completa demoró demasiado. Reintentá; no se muestran resultados parciales.')
        end = min(start + BLOCK_ROWS - 1, rows)
        block = read(source, token, config.sheet, row_range=(start, end, width)).get('values', [])
        if len(block) > end - start + 1 or any(len(row) > width for row in block):
            raise DataError('Google devolvió un bloque fuera del rango solicitado.')
        for row in block:
            if not any(value is not None and value != '' for value in row):
                continue
            row = list(row) + [None] * (width - len(row))
            key = tuple(row[i] if row[i] != '' else None for i in dimensions)
            if key not in groups:
                # Bound the accumulated dictionary before allocation, including
                # Python/string overhead; the existing raw import limit stays intact.
                group_bytes += 256 + len(outputs) * 32 + sum(64 + len(str(v)) * 4 for v in key)
                if len(groups) >= MAX_GROUPS or (len(groups) + 1) * len(outputs) > MAX_CELLS or group_bytes > MAX_GROUP_BYTES:
                    raise DataError('El resumen tiene demasiadas combinaciones. Reducí sus dimensiones; no se recortaron los datos.')
                groups[key] = [*[0] * len(counts), *[None] * len(maxima), 0]
            values = groups[key]
            for i, index in enumerate(counts):
                values[i] += int(row[index] is not None and row[index] != '')
            for i, index in enumerate(maxima, len(counts)):
                value = number(row[index], config.decimal)
                if value is None and row[index] not in (None, ''):
                    raise DataError('Una columna de máximo contiene valores no numéricos.')
                if value is not None:
                    values[i] = value if values[i] is None else max(values[i], value)
            values[-1] += 1
            source_rows += 1
        del block
    if monotonic() - started > MAX_SECONDS:
        raise DataError('La lectura completa demoró demasiado. Reintentá; no se muestran resultados parciales.')
    summary = [outputs, *[list(key) + values for key, values in groups.items()]]
    del groups
    types = {**config.types, **{name: 'number' for name in [*spec.counts, *spec.max_values, spec.row_count]}}
    frame, meta = table(summary, 1, config.decimal, types)
    if frame.memory_usage(deep=True).sum() > 96 * 1024 * 1024:
        raise DataError('El resumen supera el tamaño de memoria permitido.')
    meta.update(source_row_count=source_rows, summary_row_count=len(frame), row_count_column=spec.row_count)
    return frame, meta
