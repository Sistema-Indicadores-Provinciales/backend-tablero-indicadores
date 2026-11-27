"""Structural dependencies of production and additional chart options."""
from app.data_engine import DataError


def validate_dependencies(config):
    kind, operation = config.get('chart_type', 'bar'), config.get('aggregation', 'sum')
    indicator = kind == 'indicator'
    if (config.get('indicator_condition') or config.get('indicator_rules')) and (not indicator or operation != 'count'):
        raise DataError('La condición y las reglas del contador requieren un indicador de conteo. Editalas o quitalas explícitamente.')
    if config.get('count_col') and operation != 'count':
        raise DataError('La columna de conteo requiere Contar registros.')
    if config.get('indicator_ratio') and (not indicator or operation != 'sum'):
        raise DataError('El cociente original requiere un indicador de suma.')
    if (config.get('indicator_addends') or config.get('indicator_sum_rules')) and (not indicator or operation != 'sum' or config.get('indicator_ratio') or config.get('indicator_lookup')):
        raise DataError('La suma de columnas y la suma condicional requieren un indicador de suma sin cociente ni asignación por categoría.')
    if config.get('indicator_lookup') and (not indicator or config.get('indicator_ratio')):
        raise DataError('La asignación por categoría requiere un indicador sin cociente.')
    if config.get('indicator_text_column') and (not indicator or any(config.get(key) for key in ['indicator_ratio', 'indicator_lookup', 'indicator_addends', 'indicator_sum_rules'])):
        raise DataError('El texto publicado requiere un indicador sin mecanismos numéricos especializados.')
    if config.get('indicator_single_value_columns') and (not indicator or config.get('indicator_text_column')):
        raise DataError('La condición de valores únicos requiere un indicador numérico.')
    if config.get('metric_ratio') and (kind not in {'bar', 'horizontal', 'line', 'area'} or operation != 'sum' or any(config.get(key) for key in ['y_cols', 'include_other', 'metric_condition'])):
        raise DataError('El cociente de métricas requiere barras, líneas o área, una métrica de suma y ninguna agrupación Otros.')
    if (config.get('table_dimensions') or config.get('table_sorts')) and kind != 'table':
        raise DataError('Las dimensiones y los ordenamientos de tabla requieren el tipo Tabla.')
    if config.get('map_lookup') and kind != 'map':
        raise DataError('Las ubicaciones verificadas requieren el tipo Mapa.')
    labels = config.get('bar_labels', 'none')
    metrics = set(filter(None, [config.get('y_col'), *(config.get('y_cols') or [])]))
    if labels != 'none' and kind not in {'bar', 'horizontal', 'stacked'}:
        raise DataError('Las etiquetas de barras requieren un gráfico de barras.')
    if labels == 'second_metric' and (len(metrics) != 2 or config.get('group_col')):
        raise DataError('La etiqueta de segunda métrica necesita dos métricas sin otra separación por series.')
    if labels == 'percent_stack' and kind != 'stacked':
        raise DataError('Los porcentajes apilados requieren barras apiladas.')
