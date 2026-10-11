# HU-WFL-001 - Servidor autenticado

Fecha: 2026-10-11. Rama feat/hu-wfl-001-server-dev desde origin/develop de code-corhuila/mis-workflow. PR interno hacia develop.

## Alcance y evidencia

Servidor FastAPI/Uvicorn con inicio, consulta y reanudacion, RS256/JWKS, permisos y propiedad de saga. Conecta PostgresStore, HttpParticipants y ServiceTokenFile. Cuerpo limitado, correlacion y sobre de errores sin excepciones privadas. Imagen no root sin claves; no ejecuta migraciones al arrancar.

Pruebas de JWT y rutas incluyen rechazo de servicios como actores humanos, consulta/reanudacion por rol/propietario, 201/200/Location, validacion, conflicto y fallos correlacionados. Runtime usa Uvicorn y PostgreSQL reales; mata y reinicia el proceso, recupera la misma saga y no repite pasos. JWT de servicio separado y actores humanos se verifican criptograficamente. Participantes son servidores HTTP controlados, no APIs reales.

Pendiente: provision y migracion por ambiente, emision/renovacion operativa de SERVICE_TOKEN, Compose y gateway; validar recibo confirmado, entrada activa y un unico movimiento en APIs reales. HU-WFL-001 permanece en progreso; compensacion pertenece a HU-WFL-002. No se tocaron datos dev/qa/main.

Verificacion local: 50 pruebas correctas en Docker con PostgreSQL desechable, incluidas ocho nuevas del servidor y una de runtime. Imagen de produccion construida y verificada como UID 10001, sin directorios db/tests/secrets. Sin PostgreSQL se omiten nueve pruebas explicitamente; no confundir esa suite con validacion completa.

## Publicar

```powershell
cd "D:\descargas\pago 2\mis-workflow"
git status --short --branch
git add workflow/auth.py workflow/request_context.py workflow/server.py workflow/requirements.txt
git add tests/test_server.py tests/test_runtime.py tests/requirements.txt
git add deploy/Dockerfile .env.example README.md docs/openapi.yaml docs/HU-WFL-001-server-publicacion.md
git diff --cached --stat
git commit -m "feat(workflow): expose authenticated reception saga endpoints"
git push -u origin feat/hu-wfl-001-server-dev
```

Titulo del PR: feat(workflow): expose authenticated reception saga endpoints

```markdown
## Summary
- Expose reception start, lookup and explicit resume using the existing orchestration adapters.
- Verify human RS256 JWTs through Security JWKS and enforce roles and saga ownership.
- Keep outgoing service credentials separate from human identities.
- Add correlated, bounded request handling and a non-root runtime image.

## Validation
- Verify HTTP permissions, idempotency, payload limits, headers and safe error envelopes.
- Verify a real Uvicorn process with PostgreSQL and separate signed human/service tokens.
- Kill and restart the workflow process; retain saga state without repeating participant calls.
- Participants are controlled HTTP servers; real domain integration remains pending.

## Scope
- Environment provisioning, token issuance/renewal, Compose and gateway wiring follow in infrastructure.
- HU-WFL-001 remains in progress; compensation is not included.
```

Esperar CI verde antes de fusionar. Conservar CODEOWNERS. No se hicieron commits ni pushes automaticamente.
