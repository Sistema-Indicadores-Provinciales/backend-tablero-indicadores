"""Full outer joins after aggregation, without joining individual source rows."""
import pandas as pd

from .data_engine import DataError, MAX_ROWS, MAX_COLS, safe


def aggregate_join(left, left_meta, right, right_meta, config):
    keys = config['keys']
    left_sums = config['left_sums']
    right_sums = config['right_sums']
    dimensions = list(dict.fromkeys(keys + config.get('right_dimensions', [])))
    if len(set(keys)) != len(keys) or len(set(left_sums)) != len(left_sums) or set(keys) & set(left_sums):
        raise DataError('Las claves y las medidas de la combinación deben ser diferentes.')
    for frame, columns, metrics in [(left, keys + left_sums, left_sums),
                                    (right, dimensions + list(right_sums.values()), right_sums.values())]:
        if any(c not in frame for c in columns):
            raise DataError('La combinación usa columnas que no existen en la hoja.')
        for c in metrics:
            if any(safe(v) is not None and not isinstance(v, (int, float, bool)) for v in frame[c]):
                raise DataError(f'La medida {c} de la combinación debe ser numérica.')
    right_names = {c: f'Hoja combinada · {c}' for c in dimensions}
    output = keys + left_sums + list(right_names.values()) + list(right_sums)
    if len(output) != len(set(output)) or len(output) > MAX_COLS or set(right_sums) & set(dimensions):
        raise DataError('Los nombres de las columnas combinadas se repiten o exceden el límite.')
    # Named aggregations allow NRO MES to be both a dimension and a measure.
    a = left.groupby(keys, dropna=False, sort=False)[left_sums].sum(min_count=1).reset_index()
    b = right.groupby(dimensions, dropna=False, sort=False).agg(**{
        alias: pd.NamedAgg(column=column, aggfunc=lambda s: s.sum(min_count=1))
        for alias, column in right_sums.items()
    }).reset_index().rename(columns=right_names)
    # The left side is unique by join key: an outer join cannot exceed a+b rows.
    if len(a) + len(b) > MAX_ROWS or (len(a) + len(b)) * len(output) > 2_000_000:
        raise DataError('La combinación supera el límite de datos. Reducí las dimensiones de origen.')
    right_keys = [right_names[c] for c in keys]
    valid_a, valid_b = a[keys].notna().all(axis=1), b[right_keys].notna().all(axis=1)
    merged = a[valid_a].merge(b[valid_b], how='outer', left_on=keys, right_on=right_keys, validate='one_to_many', sort=False)
    # SQL/Looker null join keys never match; retain each side independently.
    merged = pd.concat([merged, a[~valid_a], b[~valid_b]], ignore_index=True).reindex(columns=output)
    merged = merged.astype(object).where(merged.notna(), None)
    meta = {}
    for column in output:
        original = next((k for k, v in right_names.items() if v == column), None)
        kind = (right_meta['column_meta'][original]['type'] if original else
                'number' if column in left_sums or column in right_sums else left_meta['column_meta'][column]['type'])
        values = [safe(v) for v in merged[column]]
        unique = list(dict.fromkeys(v for v in values if v is not None))
        meta[column] = {'type': kind, 'unique_values': unique[:100], 'unique_count': len(unique),
                        'missing': sum(v is None for v in values), 'invalid': 0}
    preview = [{c: safe(v) for c, v in row.items()} for row in merged.head(20).to_dict('records')]
    return merged, {'columns': output, 'column_meta': meta, 'row_count': len(merged), 'preview': preview,
                    'raw_preview': [output, *[[r[c] for c in output] for r in preview]],
                    'warnings': list(dict.fromkeys(left_meta['warnings'] + right_meta['warnings']))}
