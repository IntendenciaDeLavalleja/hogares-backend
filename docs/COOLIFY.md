# Backend y frontend en recursos independientes

## Servicios privados

Crear o reutilizar MariaDB, Redis y MinIO en Coolify, con almacenamiento persistente para sus datos.
Los nombres de host y puertos deben ser accesibles desde el contenedor del backend; usar los nombres
internos reales de los recursos y su red compartida. No es necesario exponerlos al navegador.
Crear una base de datos y un usuario exclusivo de la aplicación, y un usuario MinIO con permisos
de lectura, escritura y eliminación sobre el bucket privado `hogares-estudiantiles-documentos`.
El servicio verifica/crea el bucket al acceder; si la política no permite crearlo, crearlo previamente.

## Aplicación backend

1. Repositorio `IntendenciaDeLavalleja/hogares-backend`, rama `main`, build pack **Dockerfile**.
2. Base Directory `/`, Dockerfile Location `/Dockerfile`, puerto interno **5000**.
3. Dominio HTTPS, por ejemplo `https://api-hogares.example.uy`.
4. Cargar `.env.example` en las variables de **runtime** y completar todas las credenciales reales.
   Desactivar buildtime para los secretos; el build no necesita credenciales.
   La conexión a MariaDB usa una sola variable:

   ```dotenv
   DATABASE_URL=mariadb+pymysql://usuario:password@HOST_INTERNO_MARIADB:3306/sociales_db
   ```

   Reemplazar usuario, contraseña y host por los del recurso de MariaDB. `127.0.0.1` solo
   corresponde cuando la base está en el mismo equipo/proceso de red; dentro del contenedor
   del backend apunta al propio contenedor. Codificar los caracteres reservados de la contraseña
   en formato URL. No hace falta configurar `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` ni `DB_PASSWORD`.
5. Configurar `CORS_ORIGINS=https://hogares.example.uy` con el dominio público exacto del frontend,
   sin `/api` ni otras rutas. Varios orígenes se separan por comas. No usar `*`.
6. `TRUST_PROXY=true`, `FORCE_HTTPS=false`, `SESSION_COOKIE_SECURE=true`: Coolify termina TLS,
   mientras el healthcheck interno accede por HTTP. Las cookies del administrador siguen siendo Secure.
7. Mantener `RUN_MIGRATIONS=true`; el entrypoint aplica `flask db upgrade` y luego inicia Gunicorn.
   En despliegues con varias réplicas, aplicar las migraciones en una sola.
8. Healthcheck HTTP GET `/health/ready`, puerto 5000, estado esperado 200. Verifica DB, Redis y MinIO.

`APPLICATION_YEAR` debe coincidir con `VITE_APPLICATION_YEAR` del frontend. `MINIO_ENDPOINT` lleva
`host:puerto`, sin esquema ni ruta; `MINIO_SECURE` indica si esa conexión usa TLS. `REDIS_URL`
puede incluir autenticación. SMTP requiere elegir TLS o SSL, sin activar ambos.

Para generar claves nuevas sin publicarlas:

```bash
python scripts/generate_env.py --frontend-url https://hogares.example.uy --year 2027
```

Crea `.env.coolify` sin sobrescribir archivos existentes y sin imprimir las claves. Completar
hosts, contraseñas de servicios y SMTP antes de copiar las variables a Coolify.
Si se migra una instalación existente, **conservar sus claves originales de cifrado** para poder
leer los datos y adjuntos; no regenerarlas. Reutilizar y respaldar también DB y MinIO.

## Aplicación frontend

Crear otro recurso desde [hogares-frontend](https://github.com/IntendenciaDeLavalleja/hogares-frontend),
build pack Dockerfile, puerto **8080**, dominio `https://hogares.example.uy`.
Variables de **build**:

```dotenv
VITE_API_URL=https://api-hogares.example.uy/api
VITE_APPLICATION_YEAR=2027
```

La URL debe ser pública: el navegador llama al backend directamente. El build configura también
la CSP de Nginx para permitir ese origen. Después de cambiarla hay que reconstruir el frontend.
El frontend no lleva secretos, conexión directa a DB ni proxy fijo a un servicio `backend`.

## Verificación y administración

En la terminal del backend:

```bash
flask check-services --smtp
flask db current
flask create-admin "Nombre Admin" admin@TU-DOMINIO "UNA-CONTRASEÑA-PROPIA-SEGURA" true
```

El primer comando no envía correos. Acceder a `https://api-hogares.example.uy/admin/login`
y verificar login, código 2FA, dashboard y exportaciones. El panel y sus cookies permanecen
en el mismo dominio del backend; el formulario público usa tokens de borrador, sin cookies CORS.
En Contacto institucional, configurar el correo receptor del certificado de buena conducta.

Probar una solicitud ficticia, subida de documentos, envío con captcha, correo de confirmación y
consulta de estado desde el frontend. CORS solo habilita `/api/*` y `/auth/*` para los orígenes configurados.
No otorga acceso al panel ni reemplaza los tokens o el control de autorización.

Respaldar DB, MinIO y claves antes de actualizar. Programar `flask cleanup-drafts --limit 500`
diariamente. Los contenedores no necesitan persistir archivos locales; los datos están en DB/MinIO.

## Referencias

- [Dockerfile en Coolify](https://coolify.io/docs/applications/builds/dockerfile).
- [Variables y alcance build/runtime](https://coolify.io/docs/applications/configuration/environment-variables).
- [Variables compiladas de Vite](https://vite.dev/guide/env-and-mode.html).
