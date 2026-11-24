# Quitar el correo de los usuarios

El ingreso continúa usando nombre de usuario y contraseña. Las conexiones Google
se identifican por el ID del usuario y no se modifican.

Después de desplegar el backend sin el campo `User.email`, ejecutar desde la raíz
del backend, con `MONGODB_URL` apuntando a la base correcta:

```powershell
node migrations/remove-user-email.cjs --database NOMBRE_DE_BASE
node migrations/remove-user-email.cjs --database NOMBRE_DE_BASE --apply --backup C:/ruta/privada/respaldo-email.json
```

La primera ejecución solo informa cantidades e índices. La segunda respalda los
IDs, correos e índices afectados fuera del repositorio, elimina únicamente los
índices simples de email y aplica `$unset` a ese campo. No borra usuarios ni sus
permisos. La ruta del respaldo debe ser absoluta y no debe existir. Guardarlo de
forma privada: contiene los correos retirados. Una repetición posterior sin campos
ni índices pendientes no cambia nada.

Comprobar que se puedan crear dos cuentas distintas sin correo, ingresar con ellas,
mantener sus accesos y eliminarlas por nombre. El índice único de `username` debe
seguir presente. Los clientes antiguos que todavía envíen `email` son compatibles:
el backend ignora ese dato al crear, registrar o eliminar usuarios.
