# ADR-001 - Estado persistente de recepciones

Fecha: 2026-10-11. Estado: esquema, migracion y servicio implementados y verificados en dev, con recuperacion y aislamiento de credenciales. No se atribuye aprobacion arquitectonica al docente; promocion formal pendiente.

## Contexto

El Anexo E exige guardar el progreso despues de cada paso. Los transversales no tienen repositorio -db asignado: la ubicacion se registra en un ADR. Existe un PostgreSQL por ambiente; usar tablas de Seguridad o Inventario mezclaria propietarios y permisos.

## Propuesta

Usar un esquema workflow propio en la instancia PostgreSQL existente, con usuario workflow_app sin privilegios administrativos ni acceso a dominios ajenos. Migraciones e historial separados, propiedad de mis-workflow, ejecutados por un proceso de despliegue; el servidor HTTP no genera tablas.

Persistir una saga por actor/clave, huella del cuerpo normalizado, entrada, correlacion original, estado y prefijo de pasos confirmados. Guardar receiptId/entryId antes de avanzar. No persistir JWT humanos, SERVICE_TOKEN ni material de firma.

Serializar la ejecucion de cada saga con un bloqueo de sesion y conexion dedicada. El adaptador debe guardar progreso desde la conexion propietaria del bloqueo; si se pierde, no puede seguir escribiendo por otra conexion. No mantener transacciones de datos abiertas durante HTTP. Las respuestas perdidas se recuperan con la misma clave del participante, nunca suponiendo que no hubo efecto.

## Alternativas

| Alternativa | Costo |
| --- | --- |
| PostgreSQL con esquema propio | Migraciones, usuario y disciplina de bloqueo; reutiliza el motor existente |
| MongoDB con base propia | Otra credencial/bootstrap y estrategia de lease; posible, pero no necesaria para esta saga |
| Memoria | Solo doble de pruebas; no cumple persistencia ni recuperacion real |

## Verificacion del adaptador

db/001-sagas.sql crea esquema, historial de version y tabla con restricciones de progreso. Se ejecuta con psql por el propietario de despliegue, dentro de una transaccion y bloqueo de migracion; exige workflow_app previamente provisionado. Repetir conserva registros y permisos. Migracion inicial inmutable: cambios futuros necesitan otra version; no hay rollback destructivo automatico.

workflow/postgres.py conecta con workflow_app; solo puede insertar la intencion, leer estado y actualizar progreso, sin borrar sagas, modificar entrada/actor ni crear tablas. Guarda desde la conexion dedicada que posee el bloqueo de sesion, en autocommit: no mantiene transacciones de datos durante llamadas externas y no reconecta para guardar despues de perder el bloqueo.

Pruebas reales: migracion vacia y repetida con datos, diez creaciones concurrentes, conflicto de cuerpo, exclusion, permisos, lectura desde otro proceso, progreso por paso, terminacion del backend y recuperacion despues de efectos sin guardar. Participantes todavia son dobles idempotentes: no prueba HTTP, compensacion ni recuperacion de un contenedor workflow real.

## Pendiente para integrar

deploy/compose.yml separa la migracion/provision de rol (db/migrate.py) del servidor. La credencial administrativa no se entrega al servidor. New-WorkflowEnvironment genera password/subject propios por ambiente, y Set-WorkflowServiceToken permite emitir/renovar JWT fuera del contenedor conservando identidad. Composicion completa y gateway integrados; prueba real dev acredita persistencia, recibo confirmado, entrada activa, un movimiento, reinicio y reanudacion de fallo transitorio.

Esquema v2 agrega fase/progreso/errores de compensacion mediante 002-compensation.sql, sin modificar la migracion inicial. Conserva filas v1 con fase desconocida y no las compensa automaticamente. Ver ADR-002-compensacion.md. QA/main requieren validacion/promocion, publicacion de dominios pendientes y respaldo de saga/documentos; FAILED nunca equivale a compensacion exitosa.
