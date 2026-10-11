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

ADMIN e INVENTORY inician; INVENTORY solo consulta/reanuda propias; SUPERVISOR consulta todas, sin escribir; ADMIN administra todas. La firma RS256 se verificara en el adaptador HTTP, todavia pendiente: estas comprobaciones de roles no sustituyen autenticacion JWT.

El alcance inicial es un proveedor, medicamento y lote por recepcion, sin lineas multiples. Respuestas contienen UUID, estado, pasos completados y failedStep; no credenciales ni detalle interno. El nucleo detiene fallos transitorios sin compensar a ciegas; un rechazo definitivo queda FAILED y no se reintenta automaticamente. Compensacion y recuperacion de fallos definitivos corresponden a HU-WFL-002, aun pendiente.

Contrato en docs/openapi.yaml. El almacenamiento se registra en docs/ADR-001-saga-storage.md: esquema y usuario propios en PostgreSQL existente, sin nuevo motor ni escritura en datos de otros dominios. El adaptador PostgreSQL ya esta implementado; las recepciones MongoDB se implementaron localmente en Proveedores. Siguen pendientes despliegue, cliente HTTP, identidad de servicio, servidor, Compose y rutas de saga del gateway. Este avance no levanta un servicio ejecutable ni cierra la HU.

## Pruebas y publicacion

```powershell
python -m pip install -r tests/requirements.txt
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
