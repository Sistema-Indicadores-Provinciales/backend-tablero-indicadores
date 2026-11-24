"""Aggregate live measurements, then attach explicitly verified locations.

The location catalogue never supplies measurements and never drops unmatched
groups. No fuzzy matching or external geocoding happens at render time.
"""
import json

from .data_engine import DataError, MAX_MAP_POINTS, filter_value, summary_table


def mapped_chart(frame, cfg):
    lookup = cfg['map_lookup']
    location, category = lookup['column'], lookup.get('category_col', '')
    dimensions = [location] + ([category] if category and category != location else [])
    metrics = list(dict.fromkeys(c for c in [cfg.get('y_col'), *(cfg.get('y_cols') or [])] if c))
    result = summary_table(frame, cfg, dimensions, metrics)
    if len(result['records']) > MAX_MAP_POINTS:
        raise DataError('El mapa supera 2.000 ubicaciones. Aplicá filtros antes de generarlo.')
    metric_columns = [c for c in result['columns'] if c not in dimensions]
    points, unlocated = [], []
    for record in result['records']:
        key = filter_value(record[location])
        coordinates = lookup['locations'].get(key)
        if coordinates is None:
            unlocated.append(record)
            continue
        points.append({
            'id': json.dumps([record[c] for c in dimensions], ensure_ascii=False),
            'label': str(record[location]) if record[location] is not None else 'Sin dato',
            'lat': coordinates['lat'], 'lng': coordinates['lng'],
            'location_name': coordinates['name'],
            'location_note': coordinates.get('note', ''),
            'category': str(record[category]) if category and record[category] is not None else None,
            'value': record[metric_columns[0]] if lookup.get('sized', True) else None,
            'metrics': [{'field': c, 'value': record[c]} for c in metric_columns],
        })
    if unlocated:
        result['warnings'].append(f'{len(unlocated)} grupos sin ubicación verificada. Sus valores se conservan en el detalle del mapa.')
    return {**result, 'map_points': points, 'map_unlocated': unlocated}
