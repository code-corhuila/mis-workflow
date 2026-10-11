# ADR-002 - Compensacion por recurso

Fecha: 2026-10-11. Implementado y probado en dev; no se atribuye aprobacion arquitectonica al docente.

Preparar/confirmar recibo mutan el mismo documento: comparten void-receipt. La entrada se prepara despues del recibo; se deshacen recursos en orden inverso a su preparacion: cancel-entry, void-receipt. Esa cancelacion atomica es una guardia obligatoria, incluso si el recibo ya esta confirmado: si Inventario rechaza porque ACTIVE, no se anula recibo ni se revierte stock. Activacion permanece ultimo paso irreversible. No se pretende ejecutar una anulacion separada por cada mutacion del mismo recibo.

Rechazo definitivo conocido inicia fase guardada antes de RPC. Timeout, identidad no disponible, respuesta invalida o 409 de participante son inciertos: RUNNING requiere recuperar avance, no compensar a ciegas. Cada undo conserva clave sagaId:accion, subject y correlacion; guardar despues de cada accion permite repetir tras caida/respuesta perdida sin duplicar efectos.

COMPENSATED significa plan terminado. Un fallo de undo deja FAILED con progreso, paso y codigo privado; requiere ADMIN/compensate. Resume no reintenta FAILED. RUNNING en fase de undo puede continuar solo acciones pendientes; nunca reactivar una entrada cancelada. No almacenar tokens ni borrar historial.

Filas historicas FAILED sin fase conocida requieren reconciliacion fuera de este endpoint: no hay evidencia suficiente para deshacer un resultado posiblemente incierto. Migracion v2 conserva esos registros y sigue rechazando COMPENSATED sobre progreso incompleto o campos incoherentes. No hay downgrade/rollback destructivo automatico.

Costos aceptados: accion administrativa explicita tras fallo de undo; guardia por participante en vez de transaccion distribuida; referencia de recibo sigue ocupada aunque VOIDED. Nuevas recepciones corregidas requieren nueva referencia/intencion. Solo el workflow confiable aplica esta politica: un operador con JWT de servicio puede invocar participantes directamente y debe respetarla.
