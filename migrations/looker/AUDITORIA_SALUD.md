# Revisión de Salud — cierre actualizado 24/11/2026

Estado: **23 secciones / 166 visualizaciones validadas en prueba. Diferencias conocidas de Mortalidad y Personal corregidas y cinco mapas implementados. Publicación autorizada por el usuario el 24/11/2026, con acceso para todos los usuarios actuales.**

## Preparación de producción del 24/11

Se ejecutaron nuevamente las 186 pruebas de analytics y 22 recorridos de navegador de generador y filtros: todos aprobados. La compilación del frontend terminó correctamente, con la advertencia conocida de tamaño de bibliotecas.

La promoción conserva las cinco secciones existentes de Tablero de Salud y agrega 18, copiando las configuraciones revisadas y las referencias a 16 fuentes de Google Sheets. Incluye los orígenes particulares de cada gráfico, fórmulas, filtros iniciales, desempates y mapas. Los accesos se agregan a los cinco usuarios actuales sin retirar permisos de otros tableros. No se transfieren usuarios de prueba, contraseñas, conexiones OAuth ni filas de las hojas.

La carga se realiza con respaldo previo y una transacción de MongoDB. Los gráficos del generador anterior se conservan en su colección. El resultado de la publicación y el respaldo se guardan fuera de Git, en la carpeta privada de la migración. Cada usuario conserva la autorización de su propia cuenta Google para consultar hojas privadas.

## Validación final del 19/11

Se reemplazaron las listas fijas de desempate por selección mediante heap sobre el orden de origen. Se mantienen el campo de orden, su agregación y dirección; no se fijan cifras ni se cambia Looker. La regla se aplica a las tortas de causas/detalle de Mortalidad y Mortalidad extendido, especialidades de Personal y áreas de Indicadores detallados. El resto se recalcula desde todas las categorías excluidas, sin perder registros.

Doce comparaciones reales de las configuraciones guardadas pasaron: 226 pares categoría/valor, incluyendo el mismo orden. Se verificaron las vistas iniciales y Distrito IX en Mortalidad; Distrito IX y X en Mortalidad extendido; MEDICO y ENFERMEROS en Personal; y la vista inicial de áreas detalladas. Mortalidad IX ahora da Otros 600 y total 729; extendido inicial, Otros 1.435 y total 3.970; Personal MEDICO incluye NEUROCIRUGIA 7, Otros 107 y total 809. Esto acredita las selecciones comprobadas, no todas las combinaciones posibles.

Los cinco mapas originales usan medidas de Sheets y ubicaciones documentadas en `salud-ubicaciones.json`: GeoRef, REFES, Formosa Digital y geometrías públicas verificadas de OpenStreetMap. Los barrios usan centros asistenciales como referencia, según autorización del usuario, sin representar límites de áreas. Se verificaron 50 nombres de efectores/localidades, 38 efectores de prestaciones, 46 nombres de COVID, 68 de Dengue y 77 de áreas detalladas. Las variantes de La Primavera en COVID se identificaron mediante las coordenadas de la propia hoja. Diez comprobaciones de mapas (inicio y sin filtros) conservaron exactamente los registros y medidas del resumen.

No se asigna un punto inventado a Clorinda Área Rural, Distrito 11, OTROS/Otros ni al grupo sin localidad de Dengue. Conservan sus valores en el detalle; no se consideran establecimientos pendientes de geocodificar. La vista informa «grupos sin ubicación puntual».

Pruebas del 19/11: 186 pruebas de analytics aprobadas; dos recorridos de navegador para conservar fórmulas/desempates y mapas al editar; build del frontend aprobado, con la advertencia conocida de tamaño de bibliotecas. El 24/11 se consolidó este registro y se verificó en pantalla el listado de 23 secciones y Efectores: total 240, 50 ubicaciones, mapa cargado y filtros visibles al desplazarse. Se confirmó la configuración de desempates guardada y la conexión Google activa. No se presenta aquella ejecución como una nueva lectura de todas las hojas.

La validación se realizó en la base aislada `Indicadores_carga_secciones_20261016` y en las ramas `carga-secciones`. Durante esa validación no se modificaron el Looker original ni producción; la promoción posterior fue autorizada el 24/11.

## Alcance comprobado

Hay 23 secciones guardadas, con 166 visualizaciones: 161 gráficos/indicadores contrastados y cinco mapas. La auditoría acumulada previa compara 3.660 valores con las vistas y las exportaciones CSV del informe original, usando sus filtros iniciales. Esos valores coinciden; las fuentes se revisaron entre el 27/10 y el 16/11/2026 y no se presenta el acumulado como una lectura simultánea de todas las hojas. La validación adicional del 19/11 se detalla arriba.

Esto **no** acredita todavía equivalencia para cualquier combinación de filtros. Las referencias y los resultados de la comparación están en la carpeta local privada `.codex-runtime/carga-secciones`; no se incluyen fuentes, credenciales ni registros de pacientes en Git.

Correcciones verificadas:

- «Otros» inicial: 2.087 en Mortalidad; 1.435 en Mortalidad extendido. Total de ambas secciones: 3.970 en 2025.
- Población estimada: 617.334, consultada desde su propia hoja de Google Sheets, sin fijar el número en la configuración.
- Evolución de mortalidad: las diez series incluyen Otros y distritos I–IX, como el original; los años se ordenan de forma descendente.
- Diagnósticos intraprovinciales: se aplica la misma fórmula de texto antes de agrupar; se obtienen las 1.046 categorías del original.
- Se eliminó el recorte silencioso de tablas agregadas a 500 filas. La pantalla pagina de a 100 filas; conserva las 648 categorías extraprovinciales y las 1.046 intraprovinciales.
- Valores de camas por establecimiento y servicio: coinciden los 62 pares con datos. Los empates de cantidad pueden cambiar la posición de las barras sin cambiar el valor de cada categoría.
- Se incorporaron las etiquetas numéricas del equipo y se verificó la visualización con ambos temas. El modo nominal/porcentaje se conserva por gráfico.

## Histórico de diferencias anteriores al 19/11 (resueltas arriba)

En **Causas de mortalidad**, seleccionar Año 2025, General, Distrito IX:

| Comprobación | Looker | Sistema |
| --- | ---: | ---: |
| Total | 729 | 729 |
| Otros en Detalle de causas | 600 | 377 |

Los cinco gráficos restantes de esa vista coinciden. El detalle selecciona categorías diferentes para las primeras 19 porciones. El original ordena por `MAX(ORDEN)` descendente; varias categorías empatan y no hay un segundo criterio explícito. La prioridad de categorías observada en la vista inicial no reproduce el desempate al filtrar. No se debe confundir esta diferencia con datos faltantes ni arreglarla fijando cifras.

Se preparó, **sin aplicarla**, una alternativa para los dos detalles de mortalidad: ordenar por `SUM(TOTAL)` descendente, mostrar las 19 causas principales y agrupar el resto. Con las hojas actuales, ambos detalles darían Otros = 811 y total = 3.970 para 2025; al elegir Distrito IX, Otros = 138 y total = 729. El usuario decidió el 27/10: **«No modificar Looker; mantener pendiente esa diferencia»**. Se conserva el original y se continúa la carga con esta excepción explícita.

En **Personal de Salud**, al elegir Categoría MEDICO, coinciden ocho de nueve gráficos: contadores 809, 807, 809 y 0; tablas y ranking coincidentes. La torta de especialidades mantiene total 809 y Otros 107, pero Looker incluye NEUROCIRUGIA (7) donde el sistema incluye ENDOCRINOLOGÍA (7), por empate en el corte de 19 categorías. Esa equivalencia de categoría sigue pendiente; no se declara resuelta ni se modificó Looker.

## Secciones incorporadas desde el 27/10

- UCPIM Medicamentos y Medicamentos vencidos: importes abreviados con FLOOR; el total de vencidos reproduce el CASE por año del original. El resumen mensual y el detalle consultan pestañas diferentes.
- Prestaciones médicas: total, cobertura, rankings y distribución por ámbito; su mapa queda adaptado a una tabla agregada por efector.
- Tuberculosis: conserva el filtro fijo PERIODO >= 2021 y las sumas condicionales por zona. No se convirtió ese umbral en una ventana móvil.
- Informes diarios de COVID y Dengue: se conservan fechas iniciales, pestañas independientes, positividades publicadas como texto y diferencias entre cero y ausencia de datos. Los mapas por localidad quedan adaptados a tablas. Para Dengue se verificaron además los 13 indicadores numéricos al seleccionar 31/01/2025.
- Vacunación Dengue: cinco visualizaciones; inicio Segunda Dosis, población 3.526, aplicadas 1.800 y cobertura 51,05 %. La cobertura divide las sumas, sin promediar porcentajes de filas.
- Vacunación COVID: tres visualizaciones; inicio Dosis adicional, total 8.313. Con departamento Formosa, total 4.010 y las diez filas etarias coinciden.
- Calendario 2025: cinco visualizaciones; total 200.853. Población y cobertura devuelven ausencia si hay más de una vacuna o dosis, conforme a COUNT_DISTINCT del original. Cobertura usa el porcentaje publicado, no un cociente recalculado. Con IPV Salk y 1ra Dosis: 6.451 aplicaciones, población 6.707 y cobertura 96,18 %. Los gráficos conservan valores crudos (0,9618) y aplican formato porcentual en la vista.

- Personal de Salud: nueve visualizaciones desde ENERO 2025 - PERSONAL DE SALUD. Vista inicial: 6.546 planta total, 6.506 planta real, 809 médicos y 521 residentes. Los nueve resultados iniciales coinciden (169 valores). Tablas mantienen el detalle por distrito que Looker oculta, sin publicar nombres ni documentos personales. Nueve filtros de texto en mayúsculas; las profesiones usan Función, mientras su torta agrupa FUNCION REAL.
- Indicadores detallados: seis visualizaciones. Inicio 2025 y NACIDOS VIVOS, total 7.007. Los cinco gráficos numéricos coinciden en 54 valores; mapa provisional en tabla. Cuatro controles excluyen TASA solo en sus opciones, mientras el contador conserva su cálculo sin esa restricción. Los otros gráficos mantienen sus restricciones específicas. La torta de áreas conserva, para los empates de MAX(NUM DISTRITO), el orden de categorías observado en la vista inicial: Otros 5.907. Con Distrito IX coinciden los cinco gráficos (12 valores), total 1.131; todavía no se acredita el desempate para todas las selecciones. Los siete indicadores ofrecidos por el filtro no incluyen tasas. La fuente advierte 12 valores no numéricos en VALOR fuera de la selección auditada; no se sustituyen por ceros.

La auditoría adicional de vacunación COVID/calendario verificó 13 resultados y 25 valores con filtros. Esto no sustituye una revisión exhaustiva de todas las combinaciones.

Validación de código del 11/11: 157 pruebas de analytics, nueve recorridos de navegador del generador y build del frontend satisfactorios. Se agregó cobertura de reglas propias de los controles: conservar totales/restricciones, persistencia, permisos, validación e invalidación de datos temporales. El editor conserva esas reglas al volver a guardar. Los 12 recorridos más amplios del 27/10 siguen como evidencia histórica, no como una ejecución nueva. Se verificó en la aplicación local la selección IPV Salk/1ra Dosis y la barra de filtros fija al desplazarse. Continúan las advertencias anteriores del build por tamaño de las bibliotecas de gráficos.

## Pendientes antes de publicar

- Conservar el alcance de la evidencia: se comprobaron las selecciones documentadas, no una equivalencia universal para cualquier filtro.
- Medir la lectura de Detalle de prestaciones médicas en el entorno de destino antes de publicar: la primera carga local completa tarda aproximadamente 50–57 segundos; los filtros siguientes reutilizan la lectura y tardaron 2–3 segundos. No aumentar límites ni recortar datos para ocultar un error de lectura.
- Revisar el tablero local con el usuario antes de cualquier migración a producción o asignación a todos los administradores.

## Histórico: Suplementos cargados y contrastados (12–13/11)

Los siete gráficos están guardados en la base de prueba. Inicio 2026: 6.842.326 horas, 58.906 M de pesos y 51.618 personas. Las tres series mensuales se presentan con barras por organismo; la variación usa líneas. No se suman porcentajes de filas.

Looker y Google volvieron a permitir la consulta. Se contrastaron los 147 valores iniciales de la sección, incluidos los 36 porcentajes completos exportados de la variación. Auditoría adicional: diez comparaciones y 208 valores con Guardias, Asistencias, año 2025 y 2025/MDH, todos coincidentes. Guardias da 47.065 personas; Asistencias, 11.114. En Asistencias las horas y la variación permanecen sin valor, igual que el original; no se muestran cero ni un error de lectura por ese motivo.

Se reprodujo Datos combinados (4): unión exterior total por AÑO, MES, TIPO INDICADOR y ORGANISMO. La tabla izquierda agrupa por esas cuatro dimensiones y suma CANTIDAD HORAS/NRO MES. La derecha conserva NRO MES como dimensión adicional y suma HORAS DE MES ANTERIOR/NRO MES. Se agregan ambas hojas antes de unirlas, se preservan filas sin coincidencia y la multiplicidad definida por esas dimensiones. Los filtros se aplican antes del cociente agregado. Solo se permiten pestañas del mismo archivo autorizado, con límites explícitos de tamaño. No se modificó Looker.

Validación nueva: 163 pruebas de analytics y nueve recorridos del generador pasaron el 12/11; build del frontend confirmado el 13/11. Las pruebas cubren unión previa, claves nulas, multiplicidad, límites, credenciales del lector, cocientes filtrados y conservación de configuración al editar. Las diferencias documentadas de Mortalidad/Personal y los cinco mapas provisionales siguen pendientes.

## Detalle de prestaciones médicas (16/11)

Se cargaron los nueve gráficos desde el enlace de Google Sheets original, pestaña DETALLE ATENCIONES AMBULATORIAS. Lectura real: 834.468 filas de datos, resumidas en 103.040 combinaciones (aproximadamente 51 MB para la tabla resultante). No se recortan filas: se recorre toda la cuadrícula en bloques de 50.000. Si falla un bloque o se supera un límite, la lectura completa falla sin entregar un total parcial. Los identificadores de pacientes solo se usan para COUNT y no pasan a la vista previa ni al snapshot.

Se conservan las seis dimensiones: Año, MES, Establecimiento, Profesional, Especialidad y ObraSocial. COUNT(Paciente) se resume como Atenciones; COUNT(*) como Registros, y MAX(ORDEN) conserva el orden del gráfico mensual. Sumar esos conteos tras filtrar equivale a contar las filas originales. Los cuatro subtotales mantienen sus CASE por cobertura; no cambian las opciones de los demás gráficos. La torta por efector conserva el orden ascendente y 19 categorías más Otros; profesionales, descendente y 14 más Otros.

Vista inicial 2026: total 418.516; otras obras sociales 103.843; sin cobertura 254.405; sin especificar 29.689; IASEP 30.579. Coinciden los nueve resultados y sus 271 valores, incluyendo el CSV completo de las 224 obras sociales. Otros por efector: 350.114; por profesional: 364.286. Ninguna cifra está fijada en la configuración.

Con filtros: 17 comparaciones / 83 valores coincidentes entre enero y enero + IASEP. Enero da 61.015 atenciones y 4.601 IASEP; al seleccionar también IASEP, total e IASEP dan 4.601 y los otros tres subtotales cero. La tabla de enero completo no se cuenta como comprobada con CSV en esa auditoría adicional; sí se comprobó completa para la vista inicial y para enero + IASEP.

La lectura por bloques tiene límites propios (un millón de filas de cuadrícula, diez millones de celdas de entrada, 150.000 grupos y dos millones de celdas resumidas, presupuestos de memoria y tiempo). No modifica los límites de importación ordinaria. Usa exclusivamente la hoja autorizada y la conexión del lector. Los filtros del resumen permiten hasta 5.000 opciones; en 2026 se ofrecen los 695 profesionales y las 224 obras sociales. Se reutiliza el cálculo de opciones entre gráficos con idéntica fuente y restricciones, dentro de una misma consulta; nunca entre lectores o selecciones diferentes.

Validación del 16/11: 172 pruebas de analytics y nueve recorridos del generador satisfactorios; build del frontend satisfactorio, con las advertencias conocidas de tamaño de las bibliotecas gráficas. La configuración de lectura se conserva al editar y guardar. Continúan pendientes las excepciones de mortalidad/personal y los cinco mapas; no se declara equivalencia universal de filtros ni se publicó a producción.
