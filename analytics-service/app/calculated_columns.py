"""Bounded text expressions used by imported chart dimensions (never evaluated as code)."""
import pandas as pd

from .data_engine import DataError, MAX_COLS, filter_value, safe


def calculated_columns(frame, metadata, definitions):
    if not definitions:
        return frame, metadata
    if len(frame.columns) + len(definitions) > MAX_COLS:
        raise DataError('Las columnas calculadas superan el límite de 256 columnas.')
    result = frame.copy()
    meta = {**metadata, 'columns': list(metadata['columns']), 'column_meta': dict(metadata['column_meta'])}
    for name, parts in definitions.items():
        if name in result:
            raise DataError(f'La columna calculada {name} ya existe. Elegí otro nombre.')
        combined = pd.Series('', index=result.index, dtype='string')
        for part in parts:
            if part.get('column'):
                if part['column'] not in result:
                    raise DataError(f"No existe la columna {part['column']} de la fórmula.")
                values = result[part['column']].map(lambda v: None if safe(v) is None else filter_value(v)).astype('string')
                start = part.get('start', 1) - 1
                length = part.get('length')
                values = values.str.slice(start, None if length is None else start + length)
                case = part.get('case', 'none')
                if case == 'upper':
                    values = values.str.upper()
                elif case == 'lower':
                    values = values.str.lower()
                elif case != 'none':
                    raise DataError('Transformación de texto no válida.')
            else:
                values = part.get('literal', '')
            combined = combined + values
        result[name] = combined.astype(object).where(combined.notna(), None)
        values = list(dict.fromkeys(v for v in result[name] if v is not None))
        meta['columns'].append(name)
        meta['column_meta'][name] = {'type': 'text', 'unique_values': values[:100], 'unique_count': len(values),
                                     'missing': int(combined.isna().sum()), 'invalid': 0}
    meta['preview'] = [{c: safe(v) for c, v in row.items()} for row in result.head(20).to_dict('records')]
    return result, meta
