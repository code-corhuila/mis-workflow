import os
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row


def safe_role(role):
    forbidden = ("rolsuper", "rolcreatedb", "rolcreaterole", "rolinherit", "rolreplication", "rolbypassrls")
    return role.get("rolcanlogin") is True and all(role.get(name) is False for name in forbidden)


def admin_password():
    value = Path(os.environ["PGPASSWORD_FILE"]).read_text(encoding="utf-8").rstrip("\r\n")
    if not value or len(value) > 1024 or "\n" in value or "\r" in value or "\x00" in value:
        raise ValueError("Invalid administrator password file")
    return value


def migrate():
    password = os.environ["WORKFLOW_APP_PASSWORD"]
    if not password:
        raise ValueError("Explicit workflow password required")
    options = {"host": os.environ["PGHOST"], "dbname": os.environ["PGDATABASE"],
               "connect_timeout": 5, "options": "-c statement_timeout=10000 -c lock_timeout=5000"}
    with psycopg.connect(**options, user=os.environ["PGUSER"], password=admin_password(), autocommit=True) as admin:
        with admin.transaction():
            admin.execute("SELECT pg_advisory_xact_lock(hashtextextended('mis-workflow-role',0))")
            with admin.cursor(row_factory=dict_row) as cursor:
                role = cursor.execute("SELECT rolsuper,rolcreatedb,rolcreaterole,rolinherit,rolreplication,rolbypassrls,rolcanlogin FROM pg_roles WHERE rolname='workflow_app'").fetchone()
            if role is None:
                admin.execute(sql.SQL("CREATE ROLE workflow_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}").format(sql.Literal(password)))
            elif not safe_role(role):
                raise ValueError("Unsafe existing workflow role")
            if admin.execute("SELECT count(*) FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname='workflow_app')").fetchone()[0]:
                raise ValueError("Unexpected workflow role memberships")
        # Check an existing credential rather than silently rotating it.
        with psycopg.connect(make_conninfo(**options, user="workflow_app", password=password)) as app:
            app.execute("SELECT 1")
        if admin.execute("SELECT to_regclass('workflow.schema_version')").fetchone()[0] is None:
            source = Path(__file__).with_name("001-sagas.sql").read_text(encoding="utf-8")
            if not source.startswith("\\set ON_ERROR_STOP on\n"):
                raise ValueError("Unexpected migration format")
            admin.execute(source.split("\n", 1)[1])
        admin.execute(Path(__file__).with_name("002-compensation.sql").read_text(encoding="utf-8"))


if __name__ == "__main__":
    try:
        migrate()
    except Exception:
        raise SystemExit("Workflow provisioning/migration failed; check credentials and schema privately.") from None
    print("Workflow role and schema verified; existing credentials preserved.")
