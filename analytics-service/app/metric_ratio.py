"""Ratios of aggregated measures, calculated after the viewer's filters."""
from .data_engine import DataError, MAX_POINTS, bucket_dates, safe, restrict_frame, sort_group_values


def ratio_chart(frame, cfg):
    ratio = cfg['metric_ratio']
    x, y, group, denominator = cfg['x_col'], cfg['y_col'], cfg.get('group_col'), ratio['denominator']
    if (cfg.get('chart_type') not in {'line', 'bar', 'horizontal', 'area'} or cfg.get('aggregation', 'sum') != 'sum'
            or cfg.get('y_cols') or cfg.get('include_other') or cfg.get('metric_condition')
            or any(c not in frame for c in [x, y, denominator] + ([group] if group else []))):
        raise DataError('El cociente requiere un gráfico de líneas o barras con dos medidas de suma y sin agrupación Otros.')
    frame = restrict_frame(frame.copy(), cfg)
    for c in [y, denominator]:
        if any(safe(v) is not None and not isinstance(v, (int, float, bool)) for v in frame[c]):
            raise DataError(f'La medida {c} debe ser numérica para calcular el cociente.')
    if frame.empty:
        return {'labels': [], 'datasets': [], 'filtered_rows': 0, 'warnings': []}
    bucket_dates(frame, x, cfg.get('date_bucket', 'none'))

    def calculate(part):
        a, b = safe(part[y].sum(min_count=1)), safe(part[denominator].sum(min_count=1))
        if a is None or not b:
            return None
        return safe((a - b if ratio.get('subtract_denominator') else a) / b * ratio.get('scale', 100))

    keys = list(dict.fromkeys([x] + ([group] if group else [])))
    label = lambda v: 'Sin dato' if safe(v) is None else str(safe(v))
    matrix = {}
    for key, part in frame.groupby(keys, dropna=False, sort=False):
        key = key if isinstance(key, tuple) else (key,)
        matrix[(safe(key[0]), label(key[keys.index(group)]) if group else y)] = calculate(part)
    labels = sort_group_values(frame, x, cfg.get('category_sort'))
    groups = [(label(key), part) for key, part in frame.groupby(group, dropna=False, sort=False)] if group else [(y, frame)]
    if ratio.get('series_order'):
        scores = {name: calculate(part) for name, part in groups}
        direction = -1 if ratio['series_order'] == 'desc' else 1
        groups.sort(key=lambda item: (scores[item[0]] is None, direction * (scores[item[0]] or 0), item[0]))
    elif group and cfg.get('series_sort'):
        ranks = {label(value): i for i, value in enumerate(sort_group_values(frame, group, cfg['series_sort']))}
        groups.sort(key=lambda item: ranks[item[0]])
    warnings = []
    if cfg.get('category_limit') and len(labels) > cfg['category_limit']:
        labels = labels[:cfg['category_limit']]
        warnings.append('Se muestran las primeras categorías según el orden elegido.')
    if cfg.get('series_limit') and len(groups) > cfg['series_limit']:
        groups = groups[:cfg['series_limit']]
        warnings.append('Se muestran las primeras series según el orden elegido.')
    if len(groups) > 50 or len(labels) * len(groups) > MAX_POINTS:
        raise DataError('El gráfico supera el límite de series o puntos. Aplicá filtros.')
    return {'labels': labels, 'datasets': [{'label': name, 'data': [matrix.get((v, name)) for v in labels]} for name, _ in groups],
            'filtered_rows': len(frame), 'warnings': warnings}
