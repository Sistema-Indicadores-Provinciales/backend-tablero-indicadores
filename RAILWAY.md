# Primera prueba en Railway

Guía preparada el 22 de septiembre de 2026 para estos repositorios. No publica nada automáticamente.

La instalación usa tres servicios dentro del mismo proyecto y entorno de Railway. Solo `web` tendrá una dirección pública. MongoDB puede continuar en Atlas; `analytics` necesita un volumen para conservar los Excel subidos.

| Servicio | Repositorio | Rama | Root Directory | Puerto | Healthcheck Path |
| --- | --- | --- | --- | --- | --- |
| `backend` | `backend-tablero-indicadores` | `feat/arreglo-users` | `/` | `3000` | `/` |
| `analytics` | `backend-tablero-indicadores` | `feat/arreglo-users` | `/analytics-service` | `8000` | `/health/` |
| `web` | `frontend-tablero-indicadores` | `feat/arreglo-componentes` | `/` | `80` | `/healthz` |

Railway detecta el Dockerfile dentro de cada Root Directory. No hay que instalar Docker en tu computadora ni escribir comandos de build/start en Railway: se usan los Dockerfiles. El archivo Compose sirve para otro tipo de instalación; en esta guía los servicios se configuran desde el panel.

## 1. Subir el ajuste del frontend

El Dockerfile y `nginx.conf` del frontend ahora permiten elegir las direcciones internas mediante variables. Hay que incluir estos cambios en GitHub antes de desplegar `web`.

Desde PowerShell, en la carpeta `sistema`:

```powershell
git -C frontend-tablero-indicadores add Dockerfile nginx.conf
git -C frontend-tablero-indicadores commit -m "fix: configurar proxy web para Railway"
git -C frontend-tablero-indicadores push origin feat/arreglo-componentes
```

Esta guía y el enlace agregado a DEPLOYMENT.md también pueden guardarse en el backend:

```powershell
git -C backend-tablero-indicadores add RAILWAY.md DEPLOYMENT.md
git -C backend-tablero-indicadores commit -m "docs: agregar guia de prueba en Railway"
git -C backend-tablero-indicadores push origin feat/arreglo-users
```

## 2. Crear el proyecto y los servicios

1. Entrar a [Railway](https://railway.com/) y conectar la cuenta de GitHub que tiene acceso a los repositorios.
2. Autorizar la integración de Railway para los dos repositorios de `Sistema-Indicadores-Provinciales`. Si no aparecen, un administrador de la organización puede tener que habilitar la integración.
3. Elegir **New Project → Empty Project** y llamarlo `indicadores-prueba`.
4. Desde **Create / New**, agregar tres servicios vacíos. Nombrarlos exactamente `backend`, `analytics` y `web`.
5. En cada servicio, abrir **Settings** y configurar el Root Directory de la tabla. Conectar el repositorio en **Source** y elegir la rama correspondiente antes de lanzar el despliegue.
6. Mantener los tres en el mismo entorno y región. Usar una sola réplica; no activar suspensión automática durante la prueba.

Los proyectos nuevos de Railway tienen red privada IPv4 e IPv6. Esta configuración usa IPv4, compatible con los procesos actuales de Node y Python. Las direcciones internas se vuelven a resolver periódicamente para soportar reinicios de los otros servicios.

Fuentes: [creación y conexión de servicios](https://docs.railway.com/guides/deploying-a-monorepo), [Dockerfiles](https://docs.railway.com/builds/dockerfiles), [red privada](https://docs.railway.com/networking/private-networking/how-it-works).

## 3. Preparar MongoDB para la prueba

Recomendado: usar una copia de la base, con al menos un usuario `ADMIN` existente, y trabajar sobre datos de demostración. Crear una base vacía no alcanza: el sistema no tiene registro público ni crea un administrador inicial. Tu compañero puede preparar la copia conservando los usuarios y sus contraseñas cifradas.

Preparar una URI de Atlas que incluya explícitamente el nombre de la base de prueba. Nest y Python deben recibir exactamente la misma URI; no omitir el nombre de la base.

Crear un usuario de base de datos con lectura y escritura solo sobre esa base. No confundir este usuario de Atlas con el usuario con el que se ingresa a la aplicación.

Atlas también debe aceptar conexiones procedentes de Railway:

- Con Railway Pro, activar **Static Outbound IPs** para `backend` y `analytics`, agregar en la lista de acceso de Atlas todas las IP mostradas para ambos servicios y volver a desplegarlos.
- Para una prueba con Hobby/Trial, las IP de salida no son fijas. Una alternativa temporal es habilitar `0.0.0.0/0` en la lista de acceso de un **clúster exclusivo de prueba, sin datos sensibles**. Esto permite intentos de conexión desde cualquier IP, aunque sigue requiriendo autenticación. No aplicar ese cambio al clúster compartido con datos reales. Si la organización exige una lista restringida, usar las IP fijas del plan Pro.

Si deciden usar la base actual en vez de una copia, la web y la aplicación local compartirán usuarios, permisos y tableros. Las altas, cambios y bajas de la prueba afectarán esa misma base.

Fuentes: [acceso de red de Atlas](https://www.mongodb.com/docs/atlas/security/ip-access-list/), [IP de salida de Railway](https://docs.railway.com/networking/static-outbound-ips).

## 4. Configurar backend

En **backend → Variables**, agregar:

```dotenv
NODE_ENV=production
PORT=3000
MONGODB_URL=URI_DE_LA_BASE_DE_PRUEBA
JWT_SECRET=SECRETO_NUEVO_PARA_LA_PRUEBA
COOKIE_PATH=/api/auth/refresh
```

Reemplazar los dos valores de ejemplo. Para generar el secreto, ejecutar este comando en tu propia terminal y guardar su resultado en Railway:

```powershell
node -e "console.log(require('node:crypto').randomBytes(48).toString('hex'))"
```

No hace falta enviarlo por chat ni subir un `.env` a GitHub. El mismo secreto debe usarse en `analytics`.

En **Settings → Deploy**, configurar el healthcheck `/` y un timeout de 300 segundos. No generar dominio público para este servicio.

## 5. Configurar analytics y su disco

En **analytics → Variables**, agregar:

```dotenv
NODE_ENV=production
PORT=8000
MONGODB_URL=${{backend.MONGODB_URL}}
JWT_SECRET=${{backend.JWT_SECRET}}
ANALYTICS_STORAGE=/app/storage
RAILWAY_RUN_UID=0
```

Las expresiones `${{...}}` se pegan literalmente en Railway: toman los valores del servicio `backend`.

Crear un volumen desde el menú del proyecto (**Add Volume / Create Volume**), asociarlo a `analytics` y poner como **Mount Path**:

```text
/app/storage
```

Railway monta sus volúmenes como root; `RAILWAY_RUN_UID=0` permite escribir allí con la imagen actual. No agregar un volumen sobre `/app` ni sobre `/app/data`: esas rutas contienen código y archivos precargados incluidos en la imagen.

Configurar el healthcheck `/health/`, timeout de 300 segundos y una réplica. No generar dominio público.

Fuente: [volúmenes y permisos](https://docs.railway.com/volumes).

## 6. Configurar la web y obtener el enlace

En **web → Variables**, agregar:

```dotenv
PORT=80
API_UPSTREAM=${{backend.RAILWAY_PRIVATE_DOMAIN}}:3000
ANALYTICS_UPSTREAM=${{analytics.RAILWAY_PRIVATE_DOMAIN}}:8000
TRUST_PROXY_HEADERS=1
```

Pegar las referencias literalmente; usan el dominio privado real, que puede conservar un nombre anterior del servicio. No llevan `http://` ni una barra final. Si los servicios tienen otros nombres, ajustar la referencia o usar las direcciones que muestra Railway en **Private Networking**.

`TRUST_PROXY_HEADERS=1` se usa porque Railway entrega la IP del visitante y el protocolo HTTPS mediante sus cabeceras. Así los visitantes no comparten todos un mismo límite de solicitudes por la IP del proxy. En Docker Compose se conserva el valor predeterminado `0`.

Configurar el healthcheck `/healthz`, timeout de 300 segundos. En **Settings → Networking → Generate Domain**, crear el dominio de `web` y seleccionar **Target Port: 80**. Guardar la URL HTTPS completa, sin barra final.

El frontend ya se compila para usar `/api` y `/analytics` bajo esa misma dirección. No cargar los puertos locales de Vite ni URLs `localhost` en este despliegue.

Si van a probar los mapas, agregar `VITE_GOOGLE_MAPS_API_KEY` en `web` y autorizar el dominio público en las restricciones de esa clave. Sheets públicos funciona sin configurar Google. Las variables VITE se incorporan al compilar; cambiar una requiere un nuevo build.

Fuentes: [dominios](https://docs.railway.com/networking/domains), [cabeceras del proxy de Railway](https://docs.railway.com/networking/public-networking/specs-and-limits).

## 7. Completar CORS y desplegar

En **backend → Variables**, agregar `CORS_ORIGINS` con la URL HTTPS de `web`. Por ejemplo, si Railway asignó `indicadores-prueba-xyz.up.railway.app`:

```dotenv
CORS_ORIGINS=https://indicadores-prueba-xyz.up.railway.app
```

Ese es solo un ejemplo: usar el dominio real que Railway muestre.

En **analytics → Variables**, agregar:

```dotenv
CORS_ORIGINS=${{backend.CORS_ORIGINS}}
```

Aplicar los cambios y desplegar. Esperar a que `backend` y `analytics` estén activos; después abrir el enlace de `web`. Si alguno falló antes de completar las variables, volver a desplegarlo. Revisar en **Deploy Logs** el primer error concreto.

Los healthchecks verifican que los procesos respondan. El de analytics no prueba por sí solo el acceso a MongoDB ni que se pueda escribir en el volumen; eso se comprueba en el siguiente paso.

## 8. Probar el circuito completo

1. Abrir la URL HTTPS de `web` e iniciar sesión con un administrador de la base de prueba.
2. Crear un tablero/sección de demostración y subir un Excel pequeño.
3. Generar un gráfico y guardarlo en esa sección. Recargar y abrirlo desde el menú.
4. Asignar acceso a un segundo usuario de prueba y verificar la vista desde otra sesión.
5. Vincular una hoja pública de Sheets y generar otro gráfico.
6. Reiniciar únicamente `analytics` y comprobar que el Excel subido sigue abriendo: esto verifica el volumen.
7. Cerrar y volver a abrir sesión, y verificar que no aparecen errores de CORS ni peticiones a `localhost`.

Para Sheets privados, abrir **Administración → Conexiones**, configurar el ID y el secreto del mismo cliente OAuth y agregar el dominio HTTPS de Railway a los orígenes JavaScript autorizados de ese cliente en Google. Cada usuario debe conectar Google una vez después de habilitar la conexión persistente; luego queda asociada a su usuario y se renueva desde analytics. No poner el secreto en variables VITE. El cifrado usa una clave derivada del JWT_SECRET ya configurado (debe tener al menos 32 caracteres); se puede definir GOOGLE_TOKEN_ENCRYPTION_KEY antes de guardar credenciales, según DEPLOYMENT.md.

En modo de prueba de Google, agregar las cuentas que participarán y tener presente que Google vence las autorizaciones a los 7 días. Publicar la web en Railway no cambia el estado Testing del proyecto Google. Validar en el despliegue: conectar, abrir sección, recargar, salir e ingresar con el mismo usuario, cambiar de usuario y desconectar.

## 9. Excel ya subidos en tu computadora

Los archivos del generador están en `backend-tablero-indicadores/analytics-service/storage` o en la carpeta que indique `ANALYTICS_STORAGE` local. No viajan con GitHub. Copiar MongoDB tampoco copia esos archivos.

Para una prueba desde cero, subir nuevos archivos por el generador. Para abrir configuraciones anteriores, copiar también los archivos originales al volumen `/app/storage`, conservando sus nombres. Railway permite administrar ese volumen con `railway volume browse` o `railway volume files`; seleccionar antes el proyecto, entorno y servicio `analytics` de la prueba. Los archivos precargados bajo `analytics-service/data` sí se incluyen en la imagen.

## Costos y límites de esta primera prueba

Consultar **Usage / Billing** y configurar las alertas/límite de gasto disponibles. A la fecha de esta guía, Hobby tiene un mínimo de USD 5 mensuales con USD 5 de uso incluido; Pro tiene un mínimo de USD 20 con USD 20 incluidos. El consumo que excede ese importe se cobra aparte. No se puede asegurar que tres servicios cuesten solo el mínimo. Una prueba gratuita depende de los créditos y límites que Railway otorgue a la cuenta.

La red privada y un volumen alcanzan para este montaje inicial. No configurar réplicas en `analytics`: el volumen no se comparte entre réplicas. Antes de pasar a usuarios reales, validar copias de seguridad de MongoDB y del volumen, permisos de Atlas, presupuesto y conexión privada de Sheets.

Fuentes: [planes y precios](https://docs.railway.com/pricing/plans), [limitaciones de volúmenes](https://docs.railway.com/volumes/reference).

## Errores que se pueden encontrar

| Mensaje o síntoma | Revisar |
| --- | --- |
| `$PORT` no es un entero / no se encuentra `npm` al arrancar frontend | Dejar Custom Start Command vacío y usar el comando del Dockerfile. Puertos: backend 3000, analytics 8000, frontend 80 |
| `railway.internal could not be resolved` | Usar las referencias RAILWAY_PRIVATE_DOMAIN de arriba y el mismo entorno; no inferir el dominio a partir del nombre visible |
| No aparecen los repositorios | Permiso de la integración de Railway en la organización GitHub |
| Web responde, pero login devuelve 502 | Nombres privados, puerto 3000 y estado de `backend` |
| Generador devuelve 502 | Nombre privado, puerto 8000 y estado de `analytics` |
| MongoDB no conecta | URI, nombre de base, usuario de base de datos y lista de acceso de Atlas |
| Error 401 entre servicios | JWT_SECRET idéntico en Nest y Python; volver a iniciar sesión |
| Se cierra la sesión al renovar | COOKIE_PATH debe ser `/api/auth/refresh` y el acceso debe usar HTTPS |
| Permission denied al subir Excel | Volumen en `/app/storage`, ANALYTICS_STORAGE y RAILWAY_RUN_UID=0 |
| Desaparecen archivos después de reiniciar | Volumen faltante o montado en otra ruta |
| Fallan solo los tableros creados localmente | Falta copiar sus archivos al volumen |
| Google dice origin_mismatch | Autorizar el dominio HTTPS exacto de Railway en el cliente OAuth |

Validación local del ajuste: sintaxis y tráfico del proxy Nginx comprobados con servicios de prueba. El build de contenedores Linux, DNS privado y montaje de volumen todavía deben verificarse en Railway; este equipo no tiene Docker instalado.
