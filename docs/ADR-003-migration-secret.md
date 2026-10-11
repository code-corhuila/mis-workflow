# ADR-003 - Credencial administrativa del migrador

Decision de HU-OPS-001: workflow-db-migrate lee PGPASSWORD_FILE desde un bind de
archivo readonly, con create_host_path=false. No acepta PGPASSWORD como fallback.
El job permanece separado, no root, sin puertos ni reinicio, filesystem readonly,
cap_drop ALL y no-new-privileges. La API Workflow no recibe este montaje.

Infraestructura aprovisiona secrets/<ambiente>/workflow-admin-password desde su
configuracion local ignorada, sin imprimirlo. Repetir conserva el archivo; una
diferencia se rechaza y requiere rotacion explicita. El path se declara en el
env local de Workflow para que Compose resuelva la dependencia obligatoria.
Start-Workflow prepara el archivo; la validacion exige el secreto del ambiente.

En Linux, provisionar como administrador: el archivo requiere UID/GID 10001 y modo
0600 para que el migrador no-root pueda leerlo. En Windows, el operador debe proteger
secrets mediante ACL del host; Docker Desktop conserva el bind readonly en Linux.
No se afirma que un usuario con control del host/Docker no pueda leer secretos.

Alcance: quita la contrasena administrativa del entorno de ESTE migrador. La instancia
PostgreSQL y los otros ejecutores conservan su configuracion anterior; no se declara
todo el sistema libre de secretos en entorno. No cambia roles, esquemas ni SQL aplicado.
La rotacion del administrador debe coordinar todos los consumidores, no editar solo
el archivo montado. No usar down -v ni reescribir migraciones para esta correccion.

Esta decision acompana al commit que cambia el migrador y sus pruebas. Las pruebas
unitarias rechazan ausencia/archivo invalido y las de PostgreSQL usan un archivo
sintetico; la aceptacion del montaje/entorno real se realiza por infraestructura.
