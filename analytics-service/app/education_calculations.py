"""Calculations used by the saved Educación charts."""

import pandas as pd

from .data_engine import DataError, bucket_dates, chart, restrict_frame, safe


def numbers(frame, column):
    if column not in frame:
        raise DataError(f'El cálculo necesita la columna {column}.')
    values = pd.to_numeric(frame[column], errors='coerce')
    if (values.isna() & frame[column].notna()).any():
        raise DataError(f'La columna {column} debe ser numérica para este cálculo.')
    return values


def divide(numerator, denominator):
    return safe(numerator / denominator) if numerator is not None and denominator else None


def sum_of_extremes(frame, column):
    values = numbers(frame, column)
    lowest, highest = safe(values.min()), safe(values.max())
    return lowest + highest if lowest is not None and lowest != highest else highest


def extremes_formula(column):
    return f'IF(MIN([{column}]) != MAX([{column}]), MIN([{column}]) + MAX([{column}]), MAX([{column}]))'


def attendance_ratio(frame):
    enrollment = numbers(frame, 'MATRICULA')
    return numbers(frame, 'NUNCA ASISTIERON').div(enrollment.where(enrollment.ne(0)))


def performance_order(frame):
    column = 'nivel de desempeño'
    if column not in frame:
        raise DataError(f'El cálculo necesita la columna {column}.')
    levels = {'POR DEBAJO DEL BÁSICO': 1, 'BÁSICO': 2, 'SATISFACTORIO': 3, 'AVANZADO': 4}
    return frame[column].map(levels).fillna(5)


def pupil_percentage(frame):
    if 'ÁMBITO' not in frame:
        raise DataError('El cálculo necesita la columna ÁMBITO.')
    if frame['ÁMBITO'].nunique() > 1:
        return divide(safe(numbers(frame, 'ALUMNOS').max()), safe(numbers(frame, 'BASE DE CÁLCULO').max()))
    return divide(safe(numbers(frame, 'PORCENTAJE ALUMNOS').max()), 100)


ROW_CALCULATIONS = {
    '[NUNCA ASISTIERON] / [MATRICULA]': attendance_ratio,
    '"Total"': lambda frame: 'Total',
    ('CASE WHEN [nivel de desempeño] = "POR DEBAJO DEL BÁSICO" THEN 1 '
     'WHEN [nivel de desempeño] = "BÁSICO" THEN 2 WHEN [nivel de desempeño] = "SATISFACTORIO" THEN 3 '
     'WHEN [nivel de desempeño] = "AVANZADO" THEN 4 ELSE 5 END'): performance_order,
}
AGGREGATE_CALCULATIONS = {
    'SUM([ENTEROS DE LOS PORCENTAJES]) / SUM([VALOR ENTERO TOTAL])': lambda frame: divide(
        safe(numbers(frame, 'ENTEROS DE LOS PORCENTAJES').sum(min_count=1)),
        safe(numbers(frame, 'VALOR ENTERO TOTAL').sum(min_count=1)),
    ),
    **{
        extremes_formula(column): (lambda frame, column=column: sum_of_extremes(frame, column))
        for column in ('TOTAL DE ESTUDIANTES', 'BASE DE CÁLCULO', 'NUMERO EXTRANJEROS WEB', 'PORCENTAJE WEB')
    },
    f'MAX([BASE DE CÁLCULO]) / ({extremes_formula("TOTAL DE ESTUDIANTES")})': lambda frame: divide(
        safe(numbers(frame, 'BASE DE CÁLCULO').max()), sum_of_extremes(frame, 'TOTAL DE ESTUDIANTES')
    ),
    ('IF(COUNT_DISTINCT([ÁMBITO]) > 1, MAX([ALUMNOS]) / MAX([BASE DE CÁLCULO]), '
     'MAX([PORCENTAJE ALUMNOS]) / 100)'): pupil_percentage,
    f'SUM([ALUMNOS]) / ({extremes_formula("NUMERO EXTRANJEROS WEB")})': lambda frame: divide(
        safe(numbers(frame, 'ALUMNOS').sum(min_count=1)), sum_of_extremes(frame, 'NUMERO EXTRANJEROS WEB')
    ),
    'SUM([% DE PARTICIPACIÓN])': lambda frame: safe(numbers(frame, '% DE PARTICIPACIÓN').sum(min_count=1)),
}
CALCULATIONS = {**ROW_CALCULATIONS, **AGGREGATE_CALCULATIONS}


def education_chart(frame, config):
    frame = restrict_frame(frame, config).copy()
    config = {**config, 'filters': {}, 'filter_rules': []}
    fields = config.pop('calculated_fields')
    aggregates = {}
    for field, formula in fields.items():
        if field in frame:
            raise DataError(f'La columna calculada {field} ya existe.')
        if formula in ROW_CALCULATIONS:
            frame[field] = ROW_CALCULATIONS[formula](frame)
        elif formula in AGGREGATE_CALCULATIONS:
            aggregates[field] = AGGREGATE_CALCULATIONS[formula]
        else:
            raise DataError('La sección usa un cálculo que no está disponible.')
    if not aggregates:
        return chart(frame, config)

    # Materialize each aggregate once per category and series so the common
    # chart code can align it with the source metrics and apply display order.
    category = config.get('x_col')
    keys = list(dict.fromkeys(filter(None, (category, config.get('group_col')))))
    indicator = config.get('chart_type') == 'indicator'
    if not indicator:
        if not keys or any(key not in frame for key in keys):
            raise DataError('Elegí categorías válidas para este cálculo.')
        bucket_dates(frame, category, config.get('date_bucket', 'none'))
        config['date_bucket'] = 'none'
    for field in aggregates:
        frame[field] = None
    values = [(None, frame)] if indicator and not frame.empty else ()
    if not indicator:
        values = frame.groupby(keys, dropna=False, sort=False)
    for _, part in values:
        for field, calculate in aggregates.items():
            frame.at[part.index[0], field] = calculate(part)
    metric = config.get('y_col')
    if indicator and metric in aggregates and frame[metric].isna().all():
        return {'labels': ['Total'], 'datasets': [{'label': metric, 'data': [None]}],
                'filtered_rows': len(frame), 'warnings': []}
    return chart(frame, config)
