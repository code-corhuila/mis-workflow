# HU-WFL-001 - Publicar persistencia

Fecha: 2026-10-11. Repositorio code-corhuila/mis-workflow. Rama feat/hu-wfl-001-storage-dev creada desde origin/develop. PR dentro de la organizacion hacia develop; no hacia main y no desde el fork personal.

Este avance implementa PostgresStore y la migracion del esquema workflow. No modifica bases existentes de dev/qa/main. Aun falta integrar credenciales por ambiente, HTTP, identidad de servicio, contenedor workflow y gateway; la HU permanece en progreso. Las pruebas de participantes usan dobles, aunque la persistencia se verifica en PostgreSQL real.

## Commit y push

```powershell
cd "D:\descargas\pago 2\mis-workflow"
git status --short --branch
git add workflow/postgres.py workflow/requirements.txt db/001-sagas.sql
git add tests/test_postgres.py tests/compose.storage.yml Dockerfile.integration
git add .github/workflows/ci.yml README.md docs/ADR-001-saga-storage.md docs/HU-WFL-001-storage-publicacion.md
git diff --cached --stat
git commit -m "feat(workflow): persist reception saga progress in postgres"
git push -u origin feat/hu-wfl-001-storage-dev
```

Titulo del PR: feat(workflow): persist reception saga progress in postgres

```markdown
## Summary
- Add PostgreSQL saga persistence with actor-scoped idempotency and durable progress.
- Add an independently owned workflow schema and restricted application grants.
- Serialize saga execution with a dedicated session lock; never reconnect to save after lock loss.
- Run disposable PostgreSQL integration checks in CI.

## Validation
- Core, OpenAPI and PostgreSQL tests passed in the isolated Docker test network.
- Verify concurrent creation, lock exclusion, restricted writes, repeated migration and cross-process reads.
- Verify recovery after each participant effect before save, including uncertain activation.
- Participants remain idempotent doubles; real HTTP, workflow container restart and compensation are not covered.

## Scope
- HU-WFL-001 remains in progress pending service identity, HTTP and deployment integration.
- No development, QA or main database was modified; no credentials or RSA keys are committed.
```

Fusionar solo cuando CI este verde. No confundir la aprobacion automatica con el resultado de pruebas. QA usa cherry-pick -x segun la politica del curso; no merge entre ramas permanentes.
