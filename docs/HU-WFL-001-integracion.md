# HU-WFL-001 - Integracion operativa

Fecha: 2026-10-11. Rama feat/hu-wfl-001-integration-dev desde develop.

## Evidencia

- 55 pruebas correctas en Docker/PostgreSQL desechable: nucleo, contrato, HTTP, JWT, servidor, reinicio, migracion y Compose.
- Gateway: 13 pruebas correctas de rutas, seguridad y comportamiento existente.
- Dev real: recepcion CONFIRMED, entrada ACTIVE y exactamente un movimiento; actores humano/servicio y correlacion separados.
- Replay devuelve la misma saga sin avanzar; reinicio de workflow conserva estado.
- Detener Inventario deja RUNNING/prepare-entry con recibo guardado. Restaurarlo y renovar JWT permite resume sin duplicados.
- Migracion repetida conserva sagas, movimientos y archivo de credenciales. workflow_app no lee datos de Seguridad/Catalogo/Inventario ni borra sagas ni crea tablas.
- Credenciales y montajes separados dev/qa/main; validacion no arranca QA/main. Tres JWT propios validos y seis cruces rechazados.

## Limites

Renovacion de JWT es operacion manual autorizada, no automatica: maximo una hora y subject estable. El emisor usa la clave de Seguridad fuera de workflow. No se versionan tokens/keys/passwords; servidor solo monta JWT/clave publica. Cambios de clave RSA requieren rotacion planificada.

Esquema inicial version 1; nuevos cambios requieren otra migracion. No hay rollback destructivo automatico. Backups deben incluir saga y documentos participantes. Un fallo definitivo queda FAILED: compensacion y su recuperacion son HU-WFL-002, no incluidas.

Implementacion de HU-WFL-001 verificada en dev; cierre formal pendiente de publicar componentes de dominio aun locales, QA y promocion. No se fusiono ni se creo tag en main.

## Publicar

```powershell
cd "D:\descargas\pago 2\mis-workflow"
git add db/migrate.py deploy/Dockerfile.migrate deploy/compose.yml
git add tests/test_migrate.py tests/test_deploy.py Dockerfile.test Dockerfile.integration
git add README.md docs/openapi.yaml docs/ADR-001-saga-storage.md docs/HU-WFL-001-integracion.md
git diff --cached --stat
git commit -m "feat(workflow): deploy reception saga with isolated service identity"
git push -u origin feat/hu-wfl-001-integration-dev
```

PR interno hacia develop; titulo igual al commit. Comentario completo y pasos de otros repos en ../mis-infra-postgres/docs/HU-WFL-001-integracion-publicacion.md. CI de workflow ejecuta su suite aislada; la prueba con APIs reales es evidencia local, no parte de ese CI.
