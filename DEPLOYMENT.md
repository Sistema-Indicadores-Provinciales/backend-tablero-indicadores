# Generador de gráficos: ejecución y despliegue

Ramas: backend `feat/arreglo-users`; frontend `feat/arreglo-componentes`.
Ubicar los repositorios como carpetas hermanas. No es necesario publicar todavía.

Para la primera prueba en Railway, seguir [RAILWAY.md](RAILWAY.md): incluye los tres servicios, las ramas, las variables y el volumen de archivos.

## Desarrollo

1. En backend, `npm ci` y configurar `.env` con `MONGODB_URL`, `JWT_SECRET`, `PORT=3000`, `CORS_ORIGINS=http://localhost:5173`.
2. `JWT_SECRET` debe ser el mismo para Nest y FastAPI. FastAPI carga el `.env` del backend al iniciar.
3. En analytics-service: `python -m pip install -r requirements.txt`.
4. En backend: `npm run start:dev` y en otra terminal `npm run analytics`.
5. En frontend: `npm ci` y `npm run dev`. URLs y puertos son opcionales en desarrollo.
6. Volver a iniciar sesión: los tokens anteriores no tienen la distinción access/refresh.
7. En desarrollo, Nest y FastAPI permiten los orígenes loopback de Vite (5173–5179) y preview (4173), aunque Vite elija 5174 porque 5173 está ocupado. En producción se respeta únicamente `CORS_ORIGINS`.
8. Abrir `/generador` desde el menú. Coparticipación sigue disponible en su ruta.

La base existente necesita al menos un usuario ADMIN. El registro y la administración de usuarios requieren ahora un administrador autenticado; no se crea un usuario público automáticamente.

## Google Sheets privados y públicos

**Hojas públicas:** pegar el enlace en **Generador → Google Sheets** y pulsar **Usar hoja**. Funciona con hojas que permiten lectura a cualquier persona con el enlace y con enlaces de **Archivo → Compartir → Publicar en la Web**. No requiere cliente OAuth, clave API ni conectar una cuenta. Se importa la pestaña del enlace (`gid`); si no se indica una, Google exporta la primera disponible. Para otra pestaña, vincular su enlace. Las hojas con restricciones de descarga pueden requerir autorización o seguir sin permitir exportación según sus permisos.

El servicio descarga CSV desde Google, con límites de 25 MB, 100.000 filas, 256 columnas y 2 millones de celdas. Valida el origen y las redirecciones de Google y rechaza páginas de login/HTML. Guarda únicamente los identificadores de hoja y pestaña en `public_sheet` y `access_mode: public`; cada vista previa, gráfico o actualización vuelve a consultar Google. Los cambios de una publicación pueden tardar unos minutos. No se publican archivos ni se modifican sus permisos automáticamente.

**Hojas privadas:** un administrador configura la aplicación una sola vez:

1. Crear un proyecto Google Cloud y habilitar Google Sheets API.
2. Configurar la pantalla de consentimiento y un cliente OAuth de tipo aplicación web.
3. Registrar los orígenes JavaScript exactos: `http://localhost:5173`, `http://localhost:5174` y el dominio HTTPS público. Si se usa otro origen, registrar también ese valor. En modo de prueba, agregar las cuentas autorizadas en Google Auth Platform.
4. Ingresar al sistema como ADMIN y abrir **Administración → Conexiones** desde el menú. Pegar el ID público del cliente OAuth y guardar. Se guarda en `analytics_settings`, sin editar archivos ni reiniciar. Como alternativa, se puede configurar `GOOGLE_CLIENT_ID` en el servicio o `VITE_GOOGLE_CLIENT_ID` en frontend; el valor guardado en el sistema tiene prioridad.
5. Volver al generador, pegar el enlace, pulsar **Conectar Google**, elegir la cuenta y autorizar lectura. Si el enlace ya está escrito, se importa en el mismo paso. Con la cuenta conectada también se puede pegar otro enlace y pulsar **Usar hoja**.

`GOOGLE_SHEETS_API_KEY` sigue disponible para fuentes antiguas consultadas mediante Sheets API; no es necesaria para los enlaces públicos nuevos. Si se utiliza, restringirla a Sheets API y a las IP del servidor cuando corresponda. Las nuevas importaciones intentan primero lectura pública, incluso con una cuenta conectada; si una hoja normal requiere autorización, utilizan el token de esa cuenta (o la clave configurada). Las fuentes privadas antiguas mantienen su mecanismo de acceso; se puede volver a vincular el enlace para usar el nuevo modo público cuando corresponda.

La aplicación pide `spreadsheets.readonly`, consulta por enlace y no modifica archivos. El token Google permanece en memoria del navegador: no se guarda en MongoDB, localStorage o archivos. Al vencer o recargar la página, volver a conectar. Los archivos privados mantienen sus permisos Google; ser usuario del sistema no da acceso a cualquier hoja.

Google puede requerir verificación de la pantalla de consentimiento antes de permitir usuarios externos. No hay selector de todos los archivos de Drive ni sincronización en segundo plano; cada generación vuelve a consultar Sheets. Se leen valores formateados: elegir el formato numérico de la hoja en la vista previa.

Documentación: https://developers.google.com/identity/oauth2/web/guides/use-token-model
https://developers.google.com/workspace/sheets/api/guides/values
https://developers.google.com/workspace/sheets/api/quickstart/js

Si una hoja requiere cuenta y falta la configuración, el panel muestra un enlace a **Administración → Conexiones** para administradores. Los demás usuarios ven que deben solicitar la habilitación. El generador sigue admitiendo hojas públicas. La configuración acepta solo el ID público; no acepta secretos del cliente ni tokens personales. Hasta habilitar las privadas, se puede descargar la hoja como Excel y subirla.

Documentación de publicación: https://support.google.com/docs/answer/183965?hl=es

## Servidor web (Docker Compose)

Requisitos: Docker con Compose, un dominio con HTTPS mediante proxy inverso, acceso del servidor a Atlas. No exponer Nest ni FastAPI directamente.

1. Copiar `.env.production.example` a `.env.production`; completar los valores.
2. Generar un secreto JWT aleatorio de al menos 32 caracteres. No reutilizar el secreto anterior.
3. Desde backend ejecutar:

```sh
docker compose --env-file .env.production build
docker compose --env-file .env.production up -d
docker compose --env-file .env.production ps
```

4. El proxy HTTPS del servidor debe reenviar al puerto local `127.0.0.1:8080` (o WEB_PORT). PUBLIC_ORIGIN debe coincidir con ese dominio.
5. Probar login, actualización de sesión, subida y lectura de Excel, ambas librerías de gráficos, Sheets privado y público, guardar y volver a abrir un tablero.

El frontend llama `/api` y `/analytics` en el mismo origen. Las cookies de producción requieren HTTPS. Cambiar un valor VITE requiere reconstruir web.

Persistir y respaldar **Atlas y el volumen analytics_storage** juntos. Los archivos Excel no se guardan dentro de MongoDB: Mongo guarda dueño, identificador y configuración; el volumen guarda el archivo. Para migrar uploads de desarrollo hay que copiar también los archivos de storage. Los archivos precargados se incluyen en la imagen bajo data/.

Los contenedores y TLS deben verificarse en el servidor de destino; un build de TypeScript no valida la configuración de infraestructura.

## Alcance de esta versión

- XLSX/XLSM (valores guardados de fórmulas; macros no ejecutadas), XLS, CSV/TSV UTF-8 o Windows-1252, Google Sheets.
- Encabezados seleccionables, opción sin encabezados, nombres duplicados o vacíos conservados y diferenciados, tipos explícitos.
- Conteo, suma, promedio, mediana, mínimo, máximo y distintos. Nunca se sustituye la columna elegida por otra.
- Barras, horizontales, apiladas, líneas, área, dispersión, circular, anillo, histograma, caja, calor, tabla e indicador.
- Fuentes de sistema y privadas en analytics_sources; configuraciones en analytics_workspaces, vinculadas a dashboards y sections del sistema con permisos en users.access. Orden y ancho de gráficos configurables. Un tablero guardado usa una fuente y una hoja; configuraciones guardadas sin resultados, datos recalculados al generar o abrir el tablero guardado.
- Límites: 25 MB por archivo; 100 MB XLSX descomprimido; 100.000 filas, 256 columnas y 2 millones de celdas por hoja; 10.000 puntos y 50 series por gráfico; 30 categorías en circular/anillo; 500 filas en tabla de resultados. Se rechaza lo que supera límites, no se trunca silenciosamente el cálculo.
- No se puede prometer interpretar cualquier Excel: documentos cifrados, tablas cruzadas con varios encabezados, celdas combinadas o fórmulas sin resultado guardado pueden requerir preparar una tabla o elegir encabezados/tipos manualmente.
- No hay editor libre de diseño con arrastre, combinación de fuentes, actualización programada ni exportación a PDF.

## Pruebas

En analytics-service: `python -m pip install -r requirements-dev.txt` y `python -m unittest discover -s tests -v`.
En backend y frontend: `npm run build`.
En backend: `npx jest src/auth/auth.guard.spec.ts src/user/user.service.spec.ts src/config/cors-origins.spec.ts src/dashboard/dashboard-lifecycle.service.spec.ts --runInBand`.
En frontend: `npm run test:browser` (Chrome instalado; puerto 5190 libre).
Las pruebas usan datos temporales y dobles de Mongo/Google, sin escribir en Atlas ni requerir credenciales.

## Verificación y límites de la entrega

Se verificaron cálculos, lectura e integración de permisos con 74 pruebas del servicio, incluyendo el Excel real de Coparticipación, lectura pública de Sheets, creación de secciones, guardado en secciones existentes, permisos independientes y rechazo de reemplazos concurrentes. La suite de Nest cubre autenticación, CORS, filtrado del menú y eliminación, incluidas configuraciones de varias secciones. Las pruebas de navegador usan datos simulados para no escribir en Atlas ni pedir acceso a una cuenta Google. Se comprobó además la descarga real sin cuenta de una hoja pública de ejemplo enlazada desde la documentación de Google.

La conexión real a hojas privadas requiere configurar el cliente OAuth y probar con una cuenta autorizada. El despliegue Docker/HTTPS requiere validación en el servidor elegido; Docker no está instalado en el equipo usado para esta implementación.

Por las correcciones de seguridad, se actualizaron Nest a 11, React Router a 7 y Vite a 7. Usar Node 22.12 o superior y reinstalar dependencias con `npm ci` al traer estos cambios.

Se fijaron versiones corregidas de Multer (2.3+) y MapLibre GL (6.9+) mediante overrides. Los gráficos de mapas existentes usan Google Maps; los tipos del generador se verifican con ambas librerías.

## Tableros generados, menú y permisos

Al abrir `/generador`, `POST /v2/workspaces/restore-menu` incorpora las configuraciones anteriores del usuario al menú, inicialmente con acceso solo para su creador. La operación es idempotente y recuperable tras un fallo parcial. No vuelve a otorgar permisos revocados ni habilita elementos que un administrador haya ocultado o eliminado.

El guardado persiste la configuración y luego llama `PUT /v2/workspaces/{id}/publication`. Cuando se elige crear un tablero nuevo, crea un Dashboard y una Section con ObjectId compatibles con Nest, usando `generatedWorkspaceId` y `workspaceId` como vínculos. Al elegir una sección existente, persiste `destination: {dashboardId, sectionId}` en la configuración y vincula únicamente `Section.workspaceId`; no crea ni renombra su Dashboard. Los identificadores del tablero nuevo son estables para evitar duplicados al reintentar. Se utilizan escrituras atómicas por documento; la operación completa no es una transacción entre colecciones. Ante un error parcial, el diálogo conserva el ID, muestra el error y permite reintentar. Funciona con Atlas y con MongoDB standalone.

Los administradores seleccionan usuarios en el diálogo de guardado. Se actualiza únicamente el acceso a la sección generada; se conservan otros tableros y secciones. Los mismos accesos pueden cambiarse desde Usuarios. Los invitados pueden guardar tableros propios, pero no asignar permisos a otros usuarios.

La consulta usa `GET /v2/workspaces/{id}/view` y `GET /v2/workspaces/{id}/widgets/{widgetId}/chart`. Cada consulta verifica en Mongo el usuario actual, el tablero visible y el acceso a la sección visible y vinculada. La API aplica solo la configuración guardada: no acepta una consulta arbitraria del lector sobre una fuente privada. Los endpoints generales de fuentes siguen restringidos al propietario o a los archivos de sistema.

Compartir un tablero basado en Sheets no comparte una cuenta Google: para hojas privadas, cada lector conecta una cuenta con acceso a esa hoja. Los datos de Excel necesitan el archivo original en el almacenamiento persistente. No se guardan resultados ni tokens Google en el tablero.

Para probar manualmente: abrir Generador, recuperar un tablero anterior, guardar con otro usuario seleccionado, abrirlo desde el menú y recargar su enlace directo. Iniciar sesión con el otro usuario y verificar la vista sin edición. Revocar su acceso desde Usuarios y comprobar que deja de poder consultar los gráficos. Comprobar también el interruptor Visible desde Tableros.

### Eliminación de tableros

En **Tableros**, el interruptor solo controla la visibilidad. La papelera **Eliminar tablero completo** pide confirmación y llama a `DELETE /dashboard/:dashboardId` (ADMIN). Se aplica `deletedAt` y se retiran los accesos; las consultas del menú, la lista y el generador excluyen registros eliminados. Se conserva la fuente y su archivo. Si una sección generada está usada por otro tablero activo, se conserva su configuración para mantener esa consulta.

Eliminar una sección limpia sus referencias y permisos. Si era la última sección existente de un tablero generado, también retira ese tablero. Al abrir Tableros, `POST /dashboard/reconcile-generated` repara contenedores generados que quedaron con referencias a secciones eliminadas o inexistentes. No elimina contenedores vacíos intencionales ni secciones simplemente ocultas. Las operaciones conservan datos mediante marcas de eliminación y permiten reintentar una baja incompleta; no son una transacción entre colecciones.

### Tableros, secciones y destino del generador

La ruta raíz de cada tablero muestra tarjetas de secciones. Un administrador puede pulsar **Agregar sección**, escribir el nombre y seleccionar quién la ve. `POST /v2/dashboards/{id}/sections` agrega la sección al catálogo y actualiza solo los permisos de esa sección; recibe un `requestId` UUID para que un reintento no la duplique. No necesita subir datos para crearla. La autorización comprueba el rol actual en Mongo, no solo el del token.

Al entrar a una sección vacía, un administrador llega a `/generador?tablero={id}&seccion={id}`. El generador identifica el destino y lo conserva al subir un archivo o importar Sheets. El guardado muestra **Guardar en esta sección**. Desde el generador general, el diálogo **Dónde guardar los gráficos** permite elegir una sección vacía existente o crear un tablero nuevo. Los permisos actuales de la sección se cargan antes de habilitar el guardado.

El servidor verifica que la sección siga vinculada al tablero, no esté eliminada y no contenga otra configuración. La vinculación usa una actualización condicional: si otro usuario agrega gráficos primero, el segundo intento recibe un conflicto sin reemplazarlos. La recuperación del menú respeta el destino y omite configuraciones cuyo destino ya no está disponible. Coparticipación, que tiene una página propia, se excluye de estos destinos.

Para ampliar una sección con gráficos, abrirla y pulsar **Editar gráficos y accesos → Agregar gráfico**, luego guardar. Se actualiza la misma configuración conservando los demás gráficos. Cada configuración continúa usando una fuente; cambiarla afecta a sus gráficos. Para guardar una copia en otra sección, usar **Guardar como nuevo**. Los invitados con acceso pueden consultar gráficos, pero no crear secciones; solo el propietario de una configuración puede editarla.

Eliminar un tablero también retira las configuraciones de sus secciones que no se usen en otros tableros, conservando los archivos originales. No se renombra el tablero al editar gráficos de una sección.
