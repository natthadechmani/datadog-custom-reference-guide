# =============================================================================
#  custom_ax_audit.py — Datadog Agent custom check for AX 2012 R3 audit log
#                       ingestion (pyodbc / cross-platform variant)
# =============================================================================
#
#  WHAT THIS DOES:
#  --------------------------------------------------------------------------
#  Every minute, queries dbo.SYSDATABASELOG for new audit rows
#  (RECID > last processed) and ships each row as a Datadog log via
#  self.send_log(). The last processed RECID is stored in the Agent's
#  persistent_cache (key: last_recid) so it survives Agent restarts.
#
#  Behavior:
#  --------------------------------------------------------------------------
#  * Backfills all rows missed during downtime on the next check run.
#  * Watermark stored explicitly via read/write_persistent_cache —
#    simple key/value file on disk.
#  * Batch processing (configurable batch_size).
#
#  Deployment:
#  --------------------------------------------------------------------------
#  Place at:
#    Windows:  C:\ProgramData\Datadog\checks.d\custom_ax_audit.py
#    Linux:    /etc/datadog-agent/checks.d/custom_ax_audit.py
#  Config at:
#    Windows:  C:\ProgramData\Datadog\conf.d\custom_ax_audit.d\conf.yaml
#    Linux:    /etc/datadog-agent/conf.d/custom_ax_audit.d/conf.yaml
#
#  Naming rule (REQUIRED): the .py filename and the .d folder name must
#  share the same base (custom_ax_audit). The Datadog Agent uses that base
#  to match the check class to its configuration file.
#
#  Requirements:
#  --------------------------------------------------------------------------
#  * Datadog Agent v7.21+ (for send_log + persistent_cache API).
#  * pyodbc available in the Agent's embedded Python.
#       Check:   & "$env:ProgramFiles\Datadog\Datadog Agent\embedded3\python.exe" -c "import pyodbc"
#       Install: & "$env:ProgramFiles\Datadog\Datadog Agent\embedded3\python.exe" -m pip install pyodbc
#  * ODBC Driver for SQL Server installed on the host (Driver 17 or 18).
#  * logs_enabled: true in datadog.yaml.
#  * datadog SQL login with SELECT on dbo.SYSDATABASELOG.
#
# =============================================================================

import json

try:
    import pyodbc
    HAS_PYODBC = True
except ImportError:
    HAS_PYODBC = False

from datadog_checks.base import AgentCheck


class CustomAxAuditCheck(AgentCheck):
    """
    Polls dbo.SYSDATABASELOG and ships new audit rows to Datadog Logs.
    The last processed RECID is persisted to the Agent's persistent_cache
    under key 'last_recid' so it survives Agent restarts.
    """

    def check(self, instance):
        # ------------------------------------------------------------------
        # Guard: pyodbc must be available in the Agent's Python.
        # ------------------------------------------------------------------
        if not HAS_PYODBC:
            self.warning(
                "pyodbc not available in the Agent's embedded Python. Install it via: "
                "embedded3\\python.exe -m pip install pyodbc, and ensure an ODBC Driver "
                "for SQL Server is installed on the host."
            )
            return

        # ------------------------------------------------------------------
        # Settings from conf.yaml
        # ------------------------------------------------------------------
        host     = instance.get('host')
        username = instance.get('username')
        password = instance.get('password')
        database = instance.get('database')
        driver   = instance.get('driver', 'ODBC Driver 18 for SQL Server')
        extra_conn = instance.get('connection_string', '')

        if not all([host, username, password, database]):
            self.warning(
                "Missing required fields. conf.yaml must set host, username, "
                "password, and database."
            )
            return

        connector = instance.get('connector', 'odbc')
        if connector != 'odbc':
            self.warning(
                "connector=%s is not supported by this check (only 'odbc' / "
                "pyodbc). Update conf.yaml or use the Datadog sqlserver "
                "integration instead.", connector
            )
            return

        table_ids   = instance.get('table_ids', [211])
        lag_seconds = int(instance.get('lag_seconds', 30))
        batch_size  = int(instance.get('batch_size', 1000))
        service     = instance.get('service', 'ax-2012')
        ddsource    = instance.get('ddsource', 'ax-audit')
        tags        = list(instance.get('tags', []))

        # ------------------------------------------------------------------
        # Build the ODBC connection string.
        # ------------------------------------------------------------------
        conn_str = (
            f"DRIVER={{{driver}}};"
            f"SERVER={host};"
            f"DATABASE={database};"
            f"UID={username};"
            f"PWD={password};"
            f"{extra_conn}"
        )

        # ------------------------------------------------------------------
        # Read the last processed RECID from persistent_cache.
        # ------------------------------------------------------------------
        raw = self.read_persistent_cache('last_recid')
        last_recid = int(json.loads(raw)) if raw else 0
        self.log.info("custom_ax_audit: last_recid from cache = %d", last_recid)

        # ------------------------------------------------------------------
        # Build SQL. Sanitize table_ids to ints
        # (pyodbc parameterization doesn't accept list/array params for IN).
        # ------------------------------------------------------------------
        sanitized_tables = ','.join(str(int(t)) for t in table_ids)
        sql = (
            f"SELECT TOP (?) "
            f"    CAST(RecId AS BIGINT)                 AS recid, "
            f"    CreatedDateTime                       AS created_at, "
            f"    LogType                               AS log_type, "
            f"    CAST([Description] AS NVARCHAR(MAX))  AS description, "
            f"    [Table]                               AS table_id, "
            f"    CAST(LogRecId AS BIGINT)              AS log_recid, "
            f"    UserName                              AS user_name "
            f"FROM dbo.SYSDATABASELOG "
            f"WHERE RecId > ? "
            f"  AND CreatedDateTime < DATEADD(SECOND, -?, SYSUTCDATETIME()) "
            f"  AND [Table] IN ({sanitized_tables}) "
            f"ORDER BY RecId ASC"
        )

        # ------------------------------------------------------------------
        # Query the database.
        # ------------------------------------------------------------------
        try:
            conn = pyodbc.connect(conn_str)
            cur = conn.cursor()
            cur.execute(sql, batch_size, last_recid, lag_seconds)
            rows = cur.fetchall()
            conn.close()
        except Exception as e:
            self.warning("DB connection / query error: %s", e)
            return

        # ------------------------------------------------------------------
        # Ship each row as a Datadog log.
        # ------------------------------------------------------------------
        if not rows:
            self.log.info("custom_ax_audit: no new rows since last check")
            return

        self.log.info("custom_ax_audit: processing %d new rows", len(rows))
        for row in rows:
            self.send_log({
                'ddsource':  ddsource,
                'service':   service,
                'ddtags':    ','.join(tags) if tags else '',
                'message':   row.description if row.description else '',
                'RecId':     int(row.recid),
                'LogType':   int(row.log_type),
                'TableId':   int(row.table_id),
                'LogRecId':  int(row.log_recid),
                'UserName':  row.user_name if row.user_name else None,
                'created_at': str(row.created_at) if row.created_at else None,
            })

        # ------------------------------------------------------------------
        # Update the persistent cursor only after all rows are submitted.
        # ------------------------------------------------------------------
        self.write_persistent_cache('last_recid', json.dumps(int(rows[-1].recid)))
        self.log.info("custom_ax_audit: updated cursor to RecId=%d", int(rows[-1].recid))
