# Backend de Hogares Estudiantiles

API Flask para recepción de solicitudes de beca de alojamiento de la Intendencia de Lavalleja.

Frontend independiente: [hogares-frontend](https://github.com/IntendenciaDeLavalleja/hogares-frontend).
Para desplegar ambos recursos y conectarlos por CORS, seguir [la guía de Coolify](docs/COOLIFY.md).
Este repositorio contiene el backend en la raíz: no configurar un subdirectorio `backend` en Coolify.

## Arquitectura

- Application Factory de Flask.
- MariaDB mediante SQLAlchemy/MariaDB Connector/Python y migraciones Alembic/Flask-Migrate.
- Redis para límites compartidos entre workers.
- MinIO privado para documentos.
- Marshmallow para validación estricta y rechazo de campos desconocidos.
- Claves Fernet separadas para cifrar formularios y documentos en reposo.
- Pillow para validación, limpieza de metadata y recodificación WebP.
- Gunicorn como servidor WSGI.
- Autenticación administrativa JWT con segundo factor por correo.

## Flujo público

1. `POST /api/applications/drafts` crea un borrador y devuelve un token opaco.
2. `POST /api/applications/drafts/:id/documents` sube cada documento con `X-Draft-Token`.
3. `POST /api/applications/drafts/:id/submit` valida datos y documentos y finaliza la solicitud.
4. `GET /api/applications/receipts/:code` permite recuperar el comprobante con el mismo token.

### Confirmación y seguimiento

- Al finalizar, `contactEmail` es obligatorio, se valida y se guarda dentro del formulario cifrado.
- `GET /api/applications/drafts/:id/captcha`, con `X-Draft-Token`, genera una suma numérica
  firmada por el servidor y ligada al borrador. Expira a los diez minutos.
- Para enviar, adjuntar `X-Captcha-Token` y `X-Captcha-Answer` al `POST .../submit`.
- Después de guardar la solicitud se envía un correo con el código de seguimiento usando
  el SMTP ya configurado. No incluye documentos ni información sanitaria o familiar.
- La respuesta incluye `confirmationEmailSent`. Si SMTP falla, el trámite queda registrado;
  `POST /api/applications/drafts/:id/confirmation-email`, con `X-Draft-Token`, permite
  reintentar. Los reintentos de solicitudes ya confirmadas no duplican el correo.
- `POST /api/applications/track` recibe `trackingCode` y `email`. Permite consultar desde
  otro dispositivo y devuelve solo el estado de recepción, la fecha y los datos generales
  del trámite. No entrega el formulario ni sus documentos. Código o correo incorrectos
  producen el mismo error. Las solicitudes anteriores sin correo conservan el acceso
  al comprobante mediante su token original.

Aplicar `flask db upgrade` para agregar `confirmation_email_sent_at` (migración
`20260907_0003`). La consulta pública está en «Seguí tu trámite», en la página principal.

El token se almacena en base de datos únicamente como SHA-256. Los datos sanitarios, familiares y económicos se guardan cifrados. Los objetos de MinIO no son públicos.

## Desarrollo

Copiar `.env.example` como `.env`, configurar servicios y ejecutar:

Para desarrollo local por HTTP, usar `FLASK_ENV=development`, `TRUST_PROXY=false` y
`SESSION_COOKIE_SECURE=false`. En Coolify conservar las cookies Secure.

```bash
flask db upgrade
flask run --port 5000
```

El frontend Vite redirige `/api`, `/auth`, `/admin` y `/static` a `127.0.0.1:5000` durante desarrollo.

El servidor puede iniciarse directamente para diagnóstico con `python wsgi.py`. En producción, Docker inicia Gunicorn mediante `entrypoint.sh`; no se usa el servidor de desarrollo de Flask.

## Administradores y doble factor

Desde la raíz de este repositorio, con las variables de entorno configuradas:

```bash
python -m flask create-admin "Nombre Admin" admin@example.uy "Password1234" true
```

El último argumento acepta `true` para `super_admin` y `false` para `admin`. La contraseña exige al menos 8 caracteres, con letras y números.

Flujo compatible con `sample-backend`:

1. `POST /api/auth/login` valida email y contraseña y envía un código numérico.
2. El código se almacena con Argon2, expira en 10 minutos y admite hasta cinco intentos.
3. La respuesta entrega un JWT `2fa_pending` de 10 minutos.
4. `POST /api/auth/verify-2fa` consume el código y entrega el JWT de acceso.
5. También se mantienen los alias `/auth/login`, `/auth/verify-2fa`, `/auth/me`, `/auth/logout` y `/auth/change-password`.

Solo los `super_admin` administran cuentas. Admin y super-admin pueden consultar solicitudes y descargar documentos desde `/api/admin/applications`.

### Panel web

Abrir `http://127.0.0.1:5000/admin/login` o `/admin/login` en el dominio HTTPS del backend.
El panel usa las mismas cuentas y códigos 2FA que la API, con una sesión HttpOnly,
CSRF en todos los formularios y vencimiento absoluto de ocho horas. El cierre de sesión
revoca la cookie en Redis; desactivar una cuenta o cambiar su contraseña invalida sus sesiones.
Cuando desarrollo usa `memory://`, la revocación usa almacenamiento en memoria con vencimiento,
igual en ejecución normal y en pruebas. Ese almacenamiento dura lo que dure el proceso;
producción exige Redis para compartir y conservar las revocaciones entre procesos.
En desarrollo HTTP usar `FORCE_HTTPS=false`. En Coolify, usar `FORCE_HTTPS=false` y
`SESSION_COOKIE_SECURE=true`: el proxy exige HTTPS y el healthcheck interno usa HTTP.

| Ruta | Función | Acceso |
| --- | --- | --- |
| `/admin/login`, `/admin/2fa` | Contraseña, verificación aritmética y código por correo | Público |
| `/admin`, `/admin/`, `/admin/dashboard` | Resumen y últimas solicitudes | Admin |
| `/admin/applications` | Filtros por estado, hogar, tipo, año, fecha de inscripción y código; selección y archivado | Admin |
| `POST /admin/applications/archive` | Archiva seleccionadas o todas las filtradas | Admin |
| `/admin/archived` | Convocatorias archivadas, filtros y restauración | Admin |
| `/admin/archived/export.csv` | CSV de las solicitudes archivadas filtradas | Admin |
| `POST /admin/archived/restore` | Restaura una convocatoria archivada | Admin |
| `/admin/applications/<id>` | Formulario descifrado y documentos | Admin |
| `/admin/applications/<id>/documents/<document_id>` | Descarga privada auditada | Admin |
| `/admin/password` | Cambiar contraseña propia | Admin |
| `/admin/users` | Crear cuentas, activar/desactivar y restablecer contraseñas | Superadmin |
| `/admin/logs` | Registro de actividad con filtro y paginación | Superadmin |
| `POST /admin/logout` | Cerrar sesión | Admin |

Como en `sample-backend`, las rutas web inexistentes y los accesos sin el rol requerido
responden con la misma página HTML 404. Sin sesión, las rutas protegidas redirigen al login.
Las rutas desconocidas dentro de `/api` y `/auth` conservan errores JSON, sin redirigir al panel.
La raíz del backend devuelve HTML 404; la raíz del frontend conserva el formulario público.
El panel y sus estilos se sirven desde Flask en el dominio del backend, separados de las rutas de React.

### Exportaciones y control documental

- La lista y el CSV comparten filtros por estado, hogar, primera inscripción/renovación,
  convocatoria, rango de fecha de inscripción y código. La fecha de inscripción es la fecha de
  envío (`submitted_at`); los borradores no tienen fecha y quedan fuera cuando se usa el rango.
  El CSV descarga todos los resultados, independientemente de la página; usa UTF-8 con BOM y
  separador `;`, y neutraliza celdas que podrían ejecutarse como fórmulas.
- El archivado se hace por convocatoria desde Solicitudes. Se puede archivar una selección de
  solicitudes o todas las que coinciden con los filtros actuales, incluso en varias páginas.
  Solo se archivan solicitudes enviadas. El nombre de la convocatoria es obligatorio (3 a 120
  caracteres) y agrupa los casos en Archivados. Al archivar se guarda fecha, usuario y auditoría.
- Las solicitudes archivadas dejan de aparecer en Solicitudes, Comparación, Ponderador, el
  resumen y las exportaciones activas, pero siguen accesibles desde la ficha y el PDF. La pestaña
  Archivados permite consultarlas por convocatoria, hogar, tipo, año, fecha de inscripción y
  código, descargar un CSV con la convocatoria y la fecha de archivo, y restaurar una
  convocatoria completa para volver a trabajar con esos casos. Restaurar también queda auditado.
  Los datos se guardan en `applications.archived_at`, `archive_name` y `archived_by`.
- `/admin/ponderador` contiene únicamente la configuración del ponderador: el porcentaje de peso
  de ingreso mensual conviviente y de promedio de escolaridad. Los porcentajes se guardan en la
  tabla `ponderador_settings`, deben sumar 100% y se pueden modificar cuando sea necesario.
  No muestra solicitudes ni filtros; el ranking se aplica en Comparación.
- `/admin/comparison` muestra el ranking ponderado y, debajo, los datos declarados agrupados por
  hogar, tipo y año. Admite varios hogares y ambos tipos a la vez, usando parámetros repetidos
  (`home=minas&home=montevideo&type=new&type=renewal`). Sin selección se incluyen todas las opciones.
  El ingreso por persona suma los ingresos monetarios declarados por los convivientes y el salario
  del aspirante cuando declaró trabajar; divide entre los convivientes registrados más el aspirante.
  El formulario indica que el aspirante no debe registrarse también como conviviente. Las fichas
  sin composición suficiente o con ingresos faltantes se marcan incompletas, sin inferir ceros.
  Quien vive solo cuenta como una persona. No se suman las fichas de progenitores no convivientes.
  El ingreso se normaliza a la inversa (menor ingreso, mayor prioridad) y el promedio escolar de
  forma directa (mayor promedio, mayor puntaje), ambos de 0 a 100 dentro del conjunto filtrado.
  El puntaje ponderado se ordena de mayor a menor y los empates se resuelven por menor ingreso,
  mayor promedio y fecha de envío. Las solicitudes sin ingreso o sin promedio quedan fuera del
  ranking y se listan aparte, sin inferir valores.
  `/admin/comparison/export.csv` exporta los datos descriptivos con los mismos filtros, en orden
  cronológico y sin puntajes.
- `/admin/comparison/ranking.csv` entrega el ranking ponderado con los filtros seleccionados,
  posición, puntajes parciales, puntaje final, porcentajes usados y datos de respaldo. También se
  conservan los alias `/admin/ponderador/export.csv` y `/admin/ponderador/ranking.csv`.
  La exportación usa UTF-8 con BOM, separador `;`, protección contra fórmulas y auditoría.
  La página de Comparación muestra el ranking antes de descargarlo.
- La ponderación se calcula al consultar; solo requiere la migración `20260909_0005` para persistir
  los porcentajes.
- `/admin/applications/<id>/file.pdf` genera la ficha completa, muestra los adjuntos de imagen
  y agrega las páginas de los PDF adjuntos. Si falla un adjunto, la descarga falla con un aviso
  para evitar entregar una ficha incompleta. Las exportaciones quedan auditadas.
- `/admin/settings` permite a superadministradores configurar el correo institucional para
  certificados de buena conducta. `/api/applications/institution` publica ese correo.
  El Ministerio del Interior debe enviarlos directamente a la institución. El formulario ya
  no exige ni admite nuevas cargas de ese certificado; conserva los adjuntos históricos.
- Cada ficha permite marcar o desmarcar su recepción, registrando fecha, usuario y auditoría.
  Registrar recepción no modifica el resultado de la solicitud.

Instalar `requirements.txt` (incluye ReportLab) y ejecutar `flask db upgrade` para aplicar las
migraciones `20260908_0004` (contacto institucional y recepción de certificados), `20260909_0005`
(porcentajes del ponderador) y `20261027_0006` (archivado por convocatoria).

## Verificaciones

```bash
pytest
ruff check .
pip-audit -r requirements.txt --ignore-vuln PYSEC-2026-217
```

## Operación

La excepción puntual de auditoría corresponde a la biblioteca nativa Connector/C, cuya versión
corregida se exige al construir Docker. Ver la justificación en `docs/COOLIFY.md`.

- `/health/live` verifica el proceso.
- `/health/ready` verifica MariaDB, Redis y MinIO.
- `RUN_MIGRATIONS=true` debe estar habilitado en una sola réplica durante despliegues escalados.
- Respaldar los volúmenes de MariaDB y MinIO y probar restauraciones periódicamente.
- Conservar `DATA_ENCRYPTION_KEY` y `FILE_ENCRYPTION_KEY`: perderlas hace irrecuperables los datos cifrados.
- Conservar `JWT_SECRET_KEY` separado de `SECRET_KEY` y configurar SMTP antes de crear administradores.
- Programar `flask cleanup-drafts --limit 500` diariamente desde una tarea de Coolify.
- Rotar credenciales si se exponen y no registrar tokens de borrador ni contenido del formulario.

## Salud, convivencia y documentos condicionales

- `health.hasDisability` es obligatorio al enviar; si es verdadero, `dailyLivingSupports`
  describe los apoyos o adaptaciones cotidianos (o indica que no necesita apoyos).
- `household.motherSituation` y `fatherSituation` aceptan `lives_with`, `outside`,
  `deceased`, `unknown` y `no_contact`. Solo `outside` exige una ficha en `outsideParents`.
  Se aceptan las antiguas respuestas `livesWithMother`/`livesWithFather` para borradores previos.
- `livesAlone=true` permite cero convivientes; no permite declarar a la vez un progenitor conviviente.
- Vehículos: constancia de Tránsito solo si se declaró moto o automóvil.
- DICOSE solo si se declaró propiedad o arrendamiento de campo.
- Alquiler, contribución o cuota se exigen según el tipo de vivienda.
- Recibos de ingresos solo para ingresos con comprobante (incluye trabajo del aspirante,
  becas, jubilaciones y pensiones declaradas); actividades informales exigen su declaración
  jurada. Sin ingresos declarados no se exige un recibo de ingresos ficticio.
- Las cédulas de convivientes no se exigen cuando vive solo/a.
- Los nuevos datos se guardan en el formulario cifrado existente. No requieren columnas nuevas.
- `APPLICATION_YEAR` debe coincidir con el argumento de compilación del frontend.
- La conexión a MariaDB se configura con una única variable `DATABASE_URL`, por ejemplo
  `mariadb+mariadbconnector://root:password@127.0.0.1:3306/sociales_db`.
  En Coolify, reemplazar `127.0.0.1` por el host interno real de MariaDB y completar las credenciales.
  Si la contraseña contiene caracteres reservados de URL, deben estar codificados en la cadena.

`flask check-services --smtp` comprueba base de datos, Redis, MinIO y conexión/autenticación
SMTP sin enviar correos. Para despliegue completo, ver `docs/COOLIFY.md`.
