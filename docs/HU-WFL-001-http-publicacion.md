# HU-WFL-001 - Cliente HTTP e identidad

Fecha: 2026-10-11. Repositorio code-corhuila/mis-workflow. Rama feat/hu-wfl-001-http-dev desde origin/develop despues de fusionar persistencia. PR dentro de la organizacion hacia develop.

## Alcance

- Cliente de preparar recepcion, preparar entrada, confirmar recepcion y activar entrada.
- Clave derivada por paso y correlacion original; no se propaga JWT humano.
- Un reintento acotado con jitter solo para red, 429 y 5xx; ningun redirect o proxy del ambiente.
- Lector de JWT de servicio renovable por reemplazo externo del archivo, firma RS256 y subject estable.
- Ningun emisor, servidor workflow o montaje operativo implementado en este PR; HU-WFL-001 permanece en progreso.

Las pruebas HTTP usan servidores controlados, no las APIs reales. Las pruebas JWT usan claves sinteticas temporales. PostgreSQL real solo pertenece a la red desechable workflow_test. No se modificaron datos dev/qa/main. Ejecutar la prueba Docker desde README.md para verificar la suite completa; falta validar el flujo contra las APIs reales al desplegar el servidor.

Verificacion local: 41 pruebas correctas en Python 3.12 con PostgreSQL desechable (19 previas del nucleo/contrato, 8 de persistencia, 10 HTTP y 4 de identidad). Se detecta tambien un cuerpo truncado cuyo JSON por si solo parecia completo: debe comprobarse Content-Length antes de aceptar el resultado. Un reintento conserva la misma intencion, no crea otra operacion.

## Publicar

```powershell
cd "D:\descargas\pago 2\mis-workflow"
git status --short --branch
git add workflow/participants.py workflow/service_identity.py workflow/requirements.txt
git add tests/test_participants.py tests/test_service_identity.py
git add Dockerfile.test .github/workflows/ci.yml README.md docs/openapi.yaml
git add docs/ADR-001-saga-storage.md docs/HU-WFL-001-http-publicacion.md
git diff --cached --stat
git commit -m "feat(workflow): add participant HTTP client and service token verification"
git push -u origin feat/hu-wfl-001-http-dev
```

Titulo del PR: feat(workflow): add participant HTTP client and service token verification

```markdown
## Summary
- Map the four reception steps to supplier and inventory HTTP contracts.
- Preserve saga-derived idempotency keys and the original correlation id on bounded transient retries.
- Reject redirects, environment proxies and confidential upstream error payloads.
- Verify externally provisioned service JWT files using public RSA keys and a stable service subject.

## Validation
- Run core, OpenAPI, HTTP client, service identity and disposable PostgreSQL checks.
- Verify identical retries, partial responses, permanent rejection and credential failures.
- Verify JWT signature, issuer, audience, key id, role, service subject and renewed-file pickup.
- HTTP participants are controlled test servers; real APIs, token issuance and workflow deployment remain pending.

## Scope
- This PR does not close HU-WFL-001 or implement HU-WFL-002 compensation.
- No human tokens, service credentials or private keys are committed.
```

Esperar CI verde antes de fusionar; la aprobacion automatica no sustituye pruebas. El siguiente avance conecta servidor autenticado, migracion y credenciales por ambiente, Compose, gateway y prueba de recepcion integrada.
