# HU-WFL-002 - Seguimiento de la revision del PR 7

## Implementacion, decision y pruebas

La implementacion se fusiono primero en [PR #6](https://github.com/code-corhuila/mis-workflow/pull/6)
con [0e0f9ec](https://github.com/code-corhuila/mis-workflow/commit/0e0f9eca292367248fc895ad767d3e8d58428621):
workflow/core.py, model.py, participants.py, postgres.py, server.py, db/migrate.py,
db/002-compensation.sql, contrato y regresiones basicas. No se duplican aqui.
El [PR #7](https://github.com/code-corhuila/mis-workflow/pull/7),
[c328645](https://github.com/code-corhuila/mis-workflow/commit/c3286451af45e117ea85198e286833b4e3278f83),
agrego pruebas avanzadas y el ADR-002; depende del PR #6 ya fusionado en develop.
No es un PR de implementacion autonoma ni un cambio nuevo de las reglas productivas.

El ADR se publico tarde al separar PR para respetar el limite de 400 lineas.
Se reconoce la desviacion: estos enlaces reconstruyen la relacion, no el orden historico
decision-implementacion. No reescribir commits fusionados. En futuras HU, publicar
la decision con el comportamiento y dividir el alcance por partes coherentes, no
dejar el ADR para un PR posterior de pruebas.

## Respuesta a los hallazgos

1. Comportamiento fuera del diff: enlazado el PR #6 y su commit; el PR #7 requiere
   esa base. El seguimiento no atribuye codigo productivo al diff de pruebas.
2. ADR separado: reconocido y enlazado el commit de implementacion en ADR-002.
   El orden historico no puede corregirse retroactivamente con este seguimiento.
3. Caidas e idempotencia: el test PostgreSQL falla despues de obtener el efecto
   de un participante simulado y antes de guardar su progreso. Ahora comprueba
   explicitamente efecto existente, paso aun no persistido, nueva conexion, repeticion
   de la misma clave/correlacion, credencial renovada y un solo efecto final.
   Es PostgreSQL real con participantes simulados, NO idempotencia HTTP extremo a extremo.
4. Stock irreversible: la prueba unitaria ahora mantiene estado CONFIRMED/ACTIVE,
   saldo y un movimiento sintetico. cancel-entry devuelve ENTRY_ACTIVE; resume no
   actua y el reintento ADMIN vuelve a detenerse antes de void-receipt. Se verifica
   estado intacto, ninguna anulacion y misma clave en ambos intentos de cancelar.
   El estado del doble es una anomalia sintetica explicita, no un fallo real de Inventario.
5. Alcance/titulo: este seguimiento declara pruebas y trazabilidad, no implementacion
   de nuevas reglas. El titulo historico del PR #7 no se reescribe ni se presenta
   como evidencia de codigo productivo contenido en ese diff.

## Evidencia por nivel

- Unitario: tests/test_compensation.py; orquestador real, store y participantes dobles.
- Integracion aislada: tests/test_undo_postgres.py; PostgreSQL real, participantes dobles.
- Aceptacion multirrepositorio local: mis-infra-postgres/tests/Test-Compensation.ps1,
  publicado en [b0a1df1](https://github.com/code-corhuila/mis-infra-postgres/commit/b0a1df186b861c65d82478c3ad4120e78fa4d93a).
  Usa APIs/almacenamiento reales y proxy temporal que provoca rechazo/perdida de respuesta.
  Comprueba auditoria sin duplicacion, recuperacion ADMIN, recibo CONFIRMED,
  entrada ACTIVE, stock y movimientos intactos antes/despues del reintento.
  Esta evidencia no forma parte del diff del PR #7 ni se ejecuta en su CI.
- CI de Workflow: ejecuta pruebas unitarias y PostgreSQL desechable. La ejecucion
  local del seguimiento no implica que su futuro PR tenga checks verdes.

No cambiar 001/002 ya aplicadas, borrar evidencia ni reabrir PR fusionados.
QA/main, publicacion de dominios, secreto administrativo del migrador y renovacion
automatica de identidad siguen pendientes por separado; no cerrar Release 2 aqui.

## Verificacion y publicacion del seguimiento

Suite local ejecutada: 69 pruebas correctas con tests/compose.storage.yml y proyecto
PostgreSQL desechable workflow-review-check. No se repitio la aceptacion multirrepositorio
en este seguimiento; se enlaza la evidencia anterior. Sin cambios en runtime/migraciones.

```powershell
cd "D:\descargas\pago 2\mis-workflow"
git add tests/test_compensation.py tests/test_undo_postgres.py
git add docs/ADR-002-compensacion.md docs/HU-WFL-002-review-followup.md
git diff --cached --stat
git commit -m "fix(workflow): clarify compensation evidence and review traceability"
git push -u origin fix/hu-wfl-002-review-followup-dev
```

PR interno hacia develop, titulo igual al commit, con esta descripcion:

```markdown
## Summary
- Follow up on the automated review of #7; implementation and migration were merged in #6.
- Strengthen the stateful ACTIVE-entry guard test and explicit undo retry assertions.
- Link implementation, delayed ADR and separate real-API acceptance evidence.
- Acknowledge the late ADR publication; no production behavior or applied migration changes.
## Validation
- All 69 workflow tests passed locally with disposable PostgreSQL.
- Crash recovery uses real PostgreSQL and simulated participants, not end-to-end HTTP idempotency.
- Prior real-API evidence is linked in docs/HU-WFL-002-review-followup.md; not rerun here or claimed as CI.
- Hosted PR checks and QA promotion remain pending.
```
