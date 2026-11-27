"""Additional chart capabilities layered on production's existing chart engine."""
from functools import cmp_to_key

import pandas as pd

from app.expression_engine import CalculationPlan, ExpressionError, numeric, missing


def calculate(frame, config, legacy):
    from app.data_engine import (restrict_frame, apply_view_filters, safe, DataError,
                                 sort_group_values, natural_key, bucket_dates, MAX_POINTS, _compare_sort_items, _sort_score)
    try:
        plan = CalculationPlan(config.get('calculated_fields') or [], frame.columns)
        selected = config.get('selected_filters') or {}
        frame = plan.rows(frame, selected)
        frame = restrict_frame(frame, config)
        absent_filters = set(config.get('chart_filters') or {}) - set(frame.columns)
        if absent_filters:
            raise DataError('El filtro propio necesita una columna existente: ' + ', '.join(sorted(absent_filters)))
        frame = apply_view_filters(frame, config.get('chart_filters') or {})
        # The wrapper applies saved restrictions exactly once, before any grouping.
        cfg = {**config, 'filters': {}, 'filter_rules': [], 'chart_filters': {},
               **({'preserve_empty_categories': True} if plan.terms else {})}
        kind, operation = cfg.get('chart_type', 'bar'), cfg.get('aggregation', 'sum')
        metrics = list(dict.fromkeys(filter(None, [cfg.get('y_col'), *(cfg.get('y_cols') or [])])))
        row_table = kind == 'table' and cfg.get('table_mode', 'rows') == 'rows'
        if operation == 'none' and not row_table and not metrics:
            raise DataError('Elegí una métrica para usar Sin agregación.')
        if row_table and any(plan.modes.get(metric) == 'aggregate' for metric in metrics):
            raise DataError('Los campos agregados requieren una tabla de resumen. Los campos por fila pueden usarse en ambos modos.')
        labels = cfg.get('bar_labels', 'none')
        if labels != 'none' and kind not in {'bar', 'horizontal', 'stacked'}:
            raise DataError('Las etiquetas de barras requieren un gráfico de barras.')
        if labels == 'second_metric' and (len(metrics) != 2 or cfg.get('group_col')):
            raise DataError('La etiqueta de segunda métrica necesita dos métricas y ninguna separación por series.')
        if labels == 'percent_stack' and kind != 'stacked':
            raise DataError('Los porcentajes apilados requieren barras apiladas.')
        for metric in metrics:
            if metric not in frame and metric not in plan.terms:
                raise DataError(f'No existe la métrica [{metric}].')

        def metric_value(bucket, field, aggregation=operation):
            if plan.modes.get(field) == 'aggregate':
                value = plan.value(field, bucket, selected)
                if isinstance(value, pd.Series):
                    values = value.dropna().unique()
                    if len(values) > 1:
                        raise DataError(f'[{field}] requiere una agregación explícita.')
                    return safe(values[0]) if len(values) else None
                return safe(value)
            series = bucket[field] if field else pd.Series(1, index=bucket.index)
            if aggregation == 'count':
                return int(series.count())
            if aggregation == 'distinct':
                return int(series.nunique())
            if aggregation == 'none':
                values = series.dropna().unique()
                if len(values) > 1:
                    raise DataError(f'Sin agregación: [{field}] tiene valores diferentes en la misma categoría y serie.')
                return safe(values[0]) if len(values) else None
            series = numeric(series)
            return safe(series.sum(min_count=1) if aggregation == 'sum' else getattr(series, aggregation)())

        def order_values(column, setting, data):
            if (setting or {}).get('field') not in plan.terms or plan.modes.get((setting or {}).get('field')) != 'aggregate':
                return sort_group_values(data, column, setting, category_limit=cfg.get('category_limit'))
            field = setting['field']
            scores = [(safe(value), metric_value(bucket, field)) for value, bucket in data.groupby(column, sort=False, dropna=False)]
            direction = setting.get('direction', 'asc')
            if setting.get('tie_breaker') == 'source_heap':
                from app.selection_order import source_heap_order
                return [value for value, _ in source_heap_order(scores, lambda a, b: _compare_sort_items(('', a[1]), ('', b[1]), direction), cfg.get('category_limit'))]
            ties = {' '.join(value.split()): index for index, value in enumerate(setting.get('tie_order', []))}
            return [value for value, _ in sorted(scores, key=cmp_to_key(lambda a, b: _compare_sort_items(a, b, direction, ties)))]

        rule = cfg.get('display_condition') or {}
        if rule.get('expression') and not plan.condition(rule['expression'], frame, selected):
            return {'labels': [], 'datasets': [], 'indicator_text': rule.get('alternative', '—'),
                    'display_hidden': True, 'filtered_rows': len(frame), 'warnings': []}
        if kind == 'table' and cfg.get('table_mode', 'rows') == 'rows' and (cfg.get('table_columns') is not None or cfg.get('table_unique')):
            columns = cfg.get('table_columns')
            columns = list(frame.columns) if columns is None else columns
            if not columns or any(column not in frame for column in columns):
                raise DataError('Elegí columnas por fila válidas para la tabla.')
            output = frame[columns].drop_duplicates() if cfg.get('table_unique') else frame[columns]
            result = {'labels': [], 'datasets': [], 'columns': columns,
                      'records': [{column: safe(value) for column, value in row.items()} for row in output.head(500).to_dict('records')],
                      'filtered_rows': len(frame), 'warnings': ['Vista limitada a 500 filas.'] if len(output) > 500 else []}
        elif (any(plan.modes.get(metric) == 'aggregate' for metric in metrics) or (operation == 'none' and not row_table)
              or (kind == 'indicator' and metrics and frame[metrics[0]].dropna().map(lambda value: isinstance(value, str)).any())):
            supported = {'bar', 'horizontal', 'stacked', 'line', 'area', 'pie', 'donut', 'indicator', 'table', 'heatmap'}
            if kind not in supported:
                raise DataError('Este tipo de gráfico requiere valores por fila.')
            if any(cfg.get(key) for key in ['indicator_ratio', 'indicator_lookup', 'indicator_addends', 'indicator_sum_rules', 'metric_ratio', 'metric_condition']):
                raise DataError('El cálculo elegido se combina con un mecanismo especializado. Editá uno de los dos explícitamente.')
            if kind == 'indicator':
                value = metric_value(frame, metrics[0], 'none' if operation == 'none' or (metrics[0] in frame and frame[metrics[0]].dropna().map(lambda value: isinstance(value, str)).any()) else operation)
                result = {'labels': ['Total'], 'datasets': [{'label': metrics[0], 'metric': metrics[0], 'data': [value if not isinstance(value, str) else None]}],
                          'filtered_rows': len(frame), 'warnings': []}
                if isinstance(value, str):
                    result['indicator_text'] = value
            else:
                x, group = cfg.get('x_col', ''), cfg.get('group_col', '')
                dimensions = cfg.get('table_dimensions') or list(dict.fromkeys([x, *([group] if group else [])]))
                if any(column not in frame for column in dimensions):
                    raise DataError('Las dimensiones deben ser columnas o campos calculados por fila.')
                data = frame.copy()
                bucket_dates(data, x, cfg.get('date_bucket', 'none'))
                groups = data.groupby(dimensions, sort=False, dropna=False)
                if groups.ngroups * max(1, len(metrics)) > MAX_POINTS:
                    raise DataError('El resultado supera 10.000 valores. Aplicá filtros.')
                records, scored_records = [], []
                for values, bucket in groups:
                    values = values if isinstance(values, tuple) else (values,)
                    record = {**dict(zip(dimensions, map(safe, values))), **{metric: metric_value(bucket, metric) for metric in metrics}}
                    records.append(record)
                    scores = []
                    for setting in cfg.get('table_sorts') or []:
                        field = setting['field']
                        if field in plan.terms and plan.modes[field] == 'aggregate':
                            scores.append(metric_value(bucket, field))
                        elif field in bucket:
                            scores.append(_sort_score(bucket[field].tolist(), setting.get('aggregation', 'alphabetical')))
                        else:
                            raise DataError(f'El orden de tabla necesita la columna {field}.')
                    scored_records.append((record, scores))
                if kind == 'table':
                    sorts = cfg.get('table_sorts') or []
                    def compare(first, second):
                        for index, setting in enumerate(sorts):
                            difference = _compare_sort_items(('', first[1][index]), ('', second[1][index]), setting.get('direction', 'asc'))
                            if difference:
                                return difference
                        a = tuple(natural_key(first[0][column]) for column in dimensions)
                        b = tuple(natural_key(second[0][column]) for column in dimensions)
                        return (a > b) - (a < b)
                    records = [row for row, _ in sorted(scored_records, key=cmp_to_key(compare))]
                    result = {'labels': [], 'datasets': [], 'columns': dimensions + metrics, 'records': records, 'filtered_rows': len(frame), 'warnings': []}
                else:
                    labels = order_values(x, cfg.get('category_sort'), data)
                    series = order_values(group, cfg.get('series_sort'), data) if group else [None]
                    series = series[:cfg['series_limit']] if cfg.get('series_limit') else series
                    if len(series) * len(metrics) > 50 or len(labels) * len(series) * len(metrics) > MAX_POINTS:
                        raise DataError('El gráfico supera el límite de series o puntos.')
                    matrix = {(row[x], row.get(group), metric): row[metric] for row in records for metric in metrics}
                    datasets = [{'label': f'{metric} · {value}' if group else metric, 'metric': metric,
                                 **({'group': 'Sin dato' if value is None else str(value)} if group else {}),
                                 'data': [matrix.get((label, value, metric)) for label in labels]} for metric in metrics for value in series]
                    if any(isinstance(value, str) for dataset in datasets for value in dataset['data']):
                        raise DataError('El gráfico requiere métricas numéricas. Usá el texto como dimensión, tabla o indicador.')
                    result = {'labels': labels, 'datasets': datasets, 'filtered_rows': len(frame), 'warnings': []}
                    result = regroup_other(result, cfg, data, lambda sub, settings: calculate(sub, settings, legacy))
                    if kind in {'pie', 'donut'}:
                        if len(result['datasets']) != 1 or len(result['labels']) > 30:
                            raise DataError('Circular y anillo admiten una serie y hasta 30 categorías.')
                        values = result['datasets'][0]['data']
                        if any(value is not None and value < 0 for value in values) or not any(value is not None and value > 0 for value in values):
                            raise DataError('Circular y anillo requieren valores no negativos y al menos uno positivo.')
        else:
            result = legacy(frame, {**cfg, **({'aggregation': 'sum'} if row_table and operation == 'none' else {})})
        if cfg.get('hide_zero'):
            for dataset in result.get('datasets', []):
                dataset['data'] = [None if value == 0 else value for value in dataset['data']]
        return result
    except ExpressionError as exc:
        raise DataError(str(exc)) from exc


def regroup_other(result, config, frame, render):
    """Recompute the union of remaining records rather than combining summaries."""
    from app.data_engine import safe
    limit = config.get('category_limit')
    if not limit or len(result['labels']) <= limit:
        return result
    other = config.get('include_other', False)
    kept = limit - 1 if other else limit
    labels = result['labels'][:kept]
    remainders = {}
    if other:
        name = 'Otros'
        while name in labels:
            name += ' (resto)'
        column = config['x_col']
        remaining_keys = set(result['labels'][kept:])
        remaining = frame[frame[column].map(safe).isin(remaining_keys)].copy()
        remaining[column] = name
        settings = {**config, 'category_limit': None, 'include_other': False, 'category_sort': None}
        # Row calculations already exist in this prepared frame.
        retained = [definition for definition in settings.get('calculated_fields', []) if definition['name'] not in remaining]
        settings['calculated_fields'] = retained
        for dataset in render(remaining, settings)['datasets']:
            remainders[(dataset.get('metric'), dataset.get('group'), dataset['label'])] = dataset['data'][0] if dataset['data'] else None
        labels.append(name)
    for dataset in result['datasets']:
        identity = (dataset.get('metric'), dataset.get('group'), dataset['label'])
        dataset['data'] = dataset['data'][:kept] + ([remainders.get(identity)] if other else [])
    result['labels'] = labels
    result['warnings'].append('Se recalcularon los registros restantes en Otros.' if other else f'Se muestran {limit} categorías.')
    return result
