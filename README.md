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

Contrato en docs/openapi.yaml. El almacenamiento se propone en docs/ADR-001-saga-storage.md: esquema y usuario propios en PostgreSQL existente, sin nuevo motor ni escritura en datos de otros dominios. Sigue pendiente implementar y probar ese adaptador, las recepciones MongoDB de Proveedores, el cliente HTTP, identidad de servicio, servidor, Compose y rutas del gateway. Este avance no levanta un servicio ejecutable ni cierra la HU.

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
