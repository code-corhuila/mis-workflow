# ADR-001 - Estado persistente de recepciones

Fecha: 2026-10-11. Estado: propuesto para la siguiente integracion, no implementado ni aprobado por el docente.

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

## Verificacion pendiente

Reconstruir desde vacio, repetir migraciones, probar permisos, guardar cada paso, reiniciar el contenedor y reanudar sin duplicados, incluida activacion con respuesta perdida. Respaldo y recuperacion del estado deben conservar documentos participantes. Este PR solo prueba el contrato del puerto con dobles: no acredita estas verificaciones contra PostgreSQL.
