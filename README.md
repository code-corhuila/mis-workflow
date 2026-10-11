# mis-workflow

> Business process orchestration (saga)

Part of the **Medicine Inventory System** distributed system — team `medicine-inventory-system`, Grupo 1.
Governance and documentation live in [`mis-docs`](https://github.com/code-corhuila/mis-docs).

## Branching

Three permanent branches. **None of them accepts a direct commit** — you enter through a child
branch and leave through a Pull Request.

```
develop  <--PR--  feat/... fix/... chore/...
qa       <--PR--  qa/...
main     <--PR--  release/...  hotfix/...
```

Promotion happens **by re-application** (`git cherry-pick -x`), never by merging one permanent
branch into another: `merge develop -> qa` and `merge qa -> main` do not exist in this model.

`main` requires **1 approval from `ariel5253`**. On `develop` and `qa` the team sets its own review
rule.

Full policy: `00-governance/branching-policy.md` in `mis-docs`.

## Base de HU-WFL-001

Avance 2026-10-11: contrato OpenAPI y nucleo Python 3.12 independiente del framework. Preparar recepcion, preparar entrada, confirmar recepcion y activar entrada, en ese orden. Cada paso recibe SERVICE_TOKEN y una clave sagaId:paso, conserva correlacion y guarda progreso mediante el puerto Store. Replay del inicio devuelve la misma saga sin ejecutar pasos; resume continua explicitamente.

ADMIN e INVENTORY inician; INVENTORY solo consulta/reanuda propias; SUPERVISOR consulta todas, sin escribir; ADMIN administra todas. El adaptador HTTP verifica RS256 mediante JWKS de Seguridad; rechaza JWT de servicio como identidad humana de entrada.

El alcance inicial es un proveedor, medicamento y lote por recepcion, sin lineas multiples. Respuestas contienen UUID, estado, pasos completados y failedStep; no credenciales ni detalle interno. El nucleo detiene fallos transitorios sin compensar a ciegas; un rechazo definitivo queda FAILED y no se reintenta automaticamente. Compensacion y recuperacion de fallos definitivos corresponden a HU-WFL-002, aun pendiente.

Contrato en docs/openapi.yaml. El almacenamiento se registra en docs/ADR-001-saga-storage.md: esquema y usuario propios en PostgreSQL existente, sin nuevo motor ni escritura en datos de otros dominios. Servidor, participantes, migracion y despliegue integrados y verificados en dev. Falta publicar componentes de dominio pendientes, promover a QA y acreditar el cierre formal. HU-WFL-002 implementara compensacion; este avance no la incluye.

## Pruebas y publicacion

```powershell
python -m pip install -r tests/requirements.txt -r workflow/requirements.txt
python -m unittest discover -s tests -v
docker build -f Dockerfile.test -t mis-workflow-core-check .
```

Diecinueve pruebas correctas localmente y en Python 3.12 limpio dentro de Docker. Usan dobles, no bases ni HTTP reales: orden, idempotencia, concurrencia, permisos, respuestas invalidas, progresos incoherentes, caida antes de guardar cada paso y respuesta perdida de activacion. Recrear Runner sobre el doble no demuestra persistencia real tras reiniciar un contenedor.

Rama feat/hu-wfl-001-core-dev desde develop; PR dentro de code-corhuila/mis-workflow hacia develop. CI solo en PR a develop/qa, con cancelacion y timeout de diez minutos. Conservar CODEOWNERS; QA usa cherry-pick -x y main requiere aprobacion. No se realizaron commits ni pushes automaticamente.

```powershell
git add workflow tests docs .github/workflows/ci.yml
git add .gitignore .gitattributes .dockerignore Dockerfile.test README.md
git diff --cached --stat
git commit -m "feat(workflow): add reception saga core and contract"
git push -u origin feat/hu-wfl-001-core-dev
```

Comentario del PR:

```markdown
## Summary
- Add framework-independent reception orchestration and OpenAPI contract.
- Define idempotent steps, explicit recovery and service-credential ports.
- Propose isolated PostgreSQL saga storage; real adapters remain pending.

## Validation
- Core and schema tests passed locally using doubles.
- Real PostgreSQL persistence, participant HTTP calls and compensation are not verified by this PR.
```

## Avance de persistencia HU-WFL-001

Rama feat/hu-wfl-001-storage-dev desde develop. Adaptador PostgresStore, migracion propia db/001-sagas.sql y permisos minimos. La aplicacion no migra al arrancar; el trabajo de despliegue debe provisionar workflow_app con credencial fuera de Git y ejecutar el SQL como propietario. No almacena tokens ni claves RSA.

Prueba aislada con Docker Desktop; no publica puertos ni toca dev/qa/main. El modo trust es exclusivo de esta red interna desechable, nunca un ejemplo de configuracion de ambientes reales. Ejecutar desde la raiz de mis-workflow:

```powershell
$project = "mis-wfl-storage-check-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
try {
    docker compose -p $project -f tests/compose.storage.yml up --build --abort-on-container-exit --exit-code-from tests
    if ($LASTEXITCODE -ne 0) { throw "Fallaron las pruebas de persistencia" }
} finally {
    docker compose -p $project -f tests/compose.storage.yml down -v
}
```

La suite de integracion exige la base workflow_test y solo limpia sus tablas de prueba. Sin WORKFLOW_TEST_DSN las pruebas PostgreSQL se omiten explicitamente; CI ejecuta tambien Docker para que no baste con pruebas del nucleo. No demuestra todavia funcionamiento HTTP entre servicios ni reinicio del contenedor workflow.

Publicacion: ver docs/HU-WFL-001-storage-publicacion.md. Los comandos anteriores pertenecen al PR historico del nucleo; no reutilizarlos para este avance.

## Cliente HTTP e identidad de servicio

workflow/participants.py implementa los cuatro pasos usando origenes configurados de Proveedores e Inventario. El payload de recepcion incluye workflowId e initiatedBy; la entrada solo envia los cinco campos de su contrato. Confirmar y activar usan los UUID guardados. Nunca copia el JWT humano: Runner suministra una credencial de servicio separada.

Cada llamada conserva Idempotency-Key y correlacion originales. Timeout de socket predeterminado 1.5 segundos, configurable hasta 2; no es un limite absoluto de tiempo de pared ante respuestas lentas. Maximo dos intentos, jitter entre 50 y 150 ms solo por red, 429 o 5xx. Un timeout no demuestra ausencia de efecto: se reutiliza la intencion. Los 401/403 requieren renovar/revisar identidad y reanudar explicitamente; otros 4xx quedan como rechazo definitivo. Respuestas invalidas y redirecciones quedan inciertas, sin seguir destinos ni reintentarlos automaticamente. Se limita el cuerpo de respuesta a 64 KiB y no se almacenan errores remotos.

workflow/service_identity.py lee en cada paso un archivo SERVICE_TOKEN y verifica con una clave publica RSA, nunca privada: RS256, kid explicito, issuer mis-security, audience mis-api, service mis-workflow, rol exclusivo INVENTORY y subject estable por ambiente. Rechaza expiracion, vigencia mayor de una hora o menos de cinco segundos restantes. No emite tokens ni los renueva solo: un proceso autorizado externo debe reemplazar el archivo atomica y oportunamente, conservando subject. Montar el directorio de credenciales en solo lectura permite ver el reemplazo; un bind del archivo individual puede conservar el inode anterior. Cambiar subject rompe el alcance actor/clave de los participantes y no constituye renovacion valida. Cambiar clave/kid requiere actualizar configuracion y recrear el proveedor.

No versionar service.jwt ni claves. Ubicarlos bajo secrets/ ignorado por Git; el montaje y el emisor por ambiente se integraran en el siguiente avance. Las pruebas generan claves sinteticas en carpetas temporales y usan servidores HTTP controlados, no datos reales.

Guia del PR actual: docs/HU-WFL-001-http-publicacion.md. La suite Docker aislada verifica tambien persistencia; la prueba sin PostgreSQL omite sus ocho casos de forma explicita.

## Servidor de HU-WFL-001

workflow/server.py conecta los adaptadores sin ejecutar migraciones al arrancar. Endpoints: POST /api/v1/sagas/receive-supplies, GET /api/v1/sagas/{id}, POST /api/v1/sagas/{id}/resume. Inicio nuevo 201 con Location, repeticion 200 sin pasos; GET no ejecuta acciones. Solo humanos autenticados por RS256/issuer/audience/JWKS pueden entrar. Headers de roles no conceden permisos. La identidad saliente procede del archivo SERVICE_TOKEN, nunca del JWT humano.

El middleware limita cuerpo a 64 KiB y lectura a quince segundos, rechaza headers criticos duplicados y valida/genera correlacion. Errores conservan error/message/traceId y details por campo; no incluyen excepciones, passwords ni cuerpos remotos. /health verifica esquema y credencial de servicio vigente; no demuestra salud de todos los participantes.

Configuracion obligatoria en .env.example: password de workflow_app y base/host, rutas de JWT y clave publica montados externamente, subject estable, kid y URLs internas. El ejemplo no contiene secretos y no basta para levantar dev/qa/main: faltan bootstrap, trabajo de migracion y montajes del siguiente avance. El servicio debe usar workflow_app, nunca el usuario administrativo. No cargar una clave privada en el contenedor.

Con PostgreSQL migrado y variables ya exportadas en un entorno de prueba autorizado:

```powershell
python -m pip install -r workflow/requirements.txt
python -m uvicorn workflow.server:app --host 127.0.0.1 --port 8089 --no-access-log
```

Contenedor no root: docker build -f deploy/Dockerfile -t mis-workflow-server-check . La imagen no contiene db/, tests/ ni credenciales; migraciones se ejecutan por despliegue, no por el servidor. No publicar su puerto directamente en la composicion final: el acceso sera por gateway.

Pruebas: usar la red desechable del README. Incluyen Uvicorn real, JWT distintos humano/servicio, PostgreSQL real y reinicio del proceso. Participantes HTTP son controlados: no demuestra aun recepcion real ni stock de Inventario. Sin WORKFLOW_TEST_DSN se omiten tambien la prueba de runtime y las ocho de persistencia. Guia del PR actual: docs/HU-WFL-001-server-publicacion.md.

## Operacion integrada

Usar la composicion completa compose.workflow.yml de mis-infra-postgres; no iniciar por separado composiciones parciales. deploy/compose.yml separa workflow-db-migrate (credencial administrativa solo durante despliegue) de workflow (workflow_app, sin puertos, filesystem y credenciales en solo lectura, UID 10001). db/migrate.py provisiona un rol sin privilegios globales ni membresias, verifica credencial existente sin rotarla y aplica el SQL propio. No cambia esquemas ajenos ni ofrece rollback destructivo.

Desde mis-infra-postgres, en PowerShell 7:

```powershell
.\scripts\Start-Workflow.ps1 -Environment dev
.\scripts\Manage-Environment.ps1 -Environment dev -Action status -ComposeFile compose.workflow.yml
.\scripts\Set-WorkflowServiceToken.ps1 -Environment dev
.\tests\Test-Workflow.ps1 -VerifyRecovery -Python python
```

Start genera configuracion propia si falta, conserva password y subject existentes y renueva JWT con la clave de Seguridad. El operador debe poder leer esa clave, que nunca se copia ni monta en workflow. Renovacion manual, sin scheduler: token maximo una hora, renovar antes del vencimiento. Al vencer /health devuelve 503 y nuevas sagas quedan RUNNING sin actuar; renovar y usar resume, no cambiar subject. La carpeta montada por ambiente solo contiene public.pem/service.jwt; reemplazo atomico del JWT se ve sin reiniciar el contenedor.

Test-Workflow crea datos sinteticos y, con VerifyRecovery, interrumpe/restaura solo Inventario dev; no ejecutar simultaneamente con otras pruebas o usuarios. Requiere dependencias de tests/requirements-contract.txt de mis-infra-postgres. Valida OpenAPI sobre respuestas reales, recibo CONFIRMED, entrada ACTIVE, stock, un movimiento, replay, reinicio, recuperacion y JWT renovado. Configuraciones QA/main comprobadas sin arrancar esos ambientes; sus seis combinaciones de identidad cruzada fueron rechazadas.

Publicacion actual: docs/HU-WFL-001-integracion.md y ../mis-infra-postgres/docs/HU-WFL-001-integracion-publicacion.md. No versionar env/*.env ni secrets/. Cierre formal pendiente de publicacion de dominios, QA y promocion segun politica del curso.
