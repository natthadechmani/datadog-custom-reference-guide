# Datadog Custom Check — AX 2012 R3 Query audit log (Legacy / Agent < 7.54)

> **Use this guide when:**
> - Datadog Agent is **< 7.54**, or
> - OS is **Windows Server 2012 / 2012 R2** (hard-capped at Agent 7.47 — Datadog dropped support in 7.48).
>
> On these agents `self.send_log()` does not exist and raises:
> `AttributeError: 'CustomAxAuditCheck' object has no attribute 'send_log'`
>
> This variant writes JSON lines to a rotating file on disk; the Agent's
> built-in file log tailer ships them to Datadog. It is functionally identical
> to the `send_log` variant — same cursor, same facets, same destination.
>
> **On Agent 7.54+ use the cleaner variant instead:**
> [`gist-install-query-db-logs-custom-check.md`](./gist-install-query-db-logs-custom-check.md)

---

## What you get

- One Python check + one YAML config running inside the Datadog Agent.
- Every minute: `SELECT WHERE RecId > <last>` from `SYSDATABASELOG`, appends each row as a JSON line to a rotating log file; the Agent's file log collector tails the file and ships it to Datadog.
- Cursor stored in the Agent's `persistent_cache` → durable across restarts.
- Logs land in Datadog under `service:ax-2012 source:ax-audit` with facets `@RecId`, `@LogType`, `@TableId`, `@UserName`, `@LogRecId`.

---

## Architecture

```
   SQL Server                      Datadog Agent (on the VM)                          Datadog
 ┌─────────────┐               ┌──────────────────────────────────────┐            ┌───────────┐
 │             │               │                                      │            │           │
 │ SYSDATABASE │  ── query ──▶ │  custom_ax_audit.py  ─ JSON lines ─▶ │ ── TLS ──▶ │  Logs     │
 │   LOG       │   (pyodbc)    │   (every 60s)          ax_audit.log  │   443      │  intake   │
 │             │               │                       (file tailer)  │            │           │
 └─────────────┘               └──────┬───────────────┬───────────────┘            └───────────┘
                                      │ reads         │ reads / writes
                                      ▼               ▼
                                 conf.yaml        last_recid
                                 (settings)       (cursor file)
```

Every 60 seconds the check:

1. Reads the cursor from `last_recid` (persistent_cache).
2. Queries SQL Server for rows `WHERE RecId > <cursor>`.
3. Appends each row as a JSON line to `ax_audit.log` (rotating, 20 MB × 5 files).
4. The Agent's file log collector tails that file and ships lines to Datadog.
5. Writes the new cursor back to `last_recid`.

### Files

| File | Purpose |
|---|---|
| `checks.d\custom_ax_audit.py` | The check code. |
| `conf.d\custom_ax_audit.d\conf.yaml` | DB creds, table_ids, `logs:` block. |
| `run\custom_ax_audit\last_recid` | The persistent cursor (JSON int). |
| `logs\custom_ax_audit\ax_audit.log` | JSON-lines output tailed by the Agent. Rotates at 20 MB, keeps 5 files. |

---

## Prerequisites

### 1. Datadog Agent v7.x (any version)

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" version
```

This variant uses only the file log collection API — compatible with **any Agent 7.x release**.

> **Windows Server 2012 / 2012 R2:** Agent 7.48 dropped support for these OS versions.
> Install the latest **7.47.x** release and pin it — do not use `latest`.
> Find the highest 7.47.x tag at <https://github.com/DataDog/datadog-agent/releases>.

Install from <https://docs.datadoghq.com/agent/> if missing.

### 2. `logs_enabled: true` in `datadog.yaml`

Edit `C:\ProgramData\Datadog\datadog.yaml`:

```yaml
logs_enabled: true
```

### 3. SQL Server in Mixed Mode

```sql
SELECT SERVERPROPERTY('IsIntegratedSecurityOnly');  -- 0 = Mixed (good), 1 = Windows-only (bad)
```

If `1`, switch to Mixed Mode:

```sql
EXEC xp_instance_regwrite
    N'HKEY_LOCAL_MACHINE',
    N'Software\Microsoft\MSSQLServer\MSSQLServer',
    N'LoginMode', REG_DWORD, 2;
```

Then `Restart-Service -Name 'MSSQLSERVER' -Force`.

### 4. `datadog` SQL login with SELECT on `dbo.SYSDATABASELOG`

```sql
USE master;
CREATE LOGIN datadog WITH PASSWORD = '<strong-password>',
                          CHECK_POLICY = OFF, CHECK_EXPIRATION = OFF;
CREATE USER datadog FOR LOGIN datadog;
GRANT CONNECT TO datadog;

USE <ax_database>;     -- e.g. MicrosoftDynamicsAX
CREATE USER datadog FOR LOGIN datadog;
GRANT SELECT ON OBJECT::dbo.SYSDATABASELOG TO datadog;
```

### 5. Confirm the AX database name

```sql
SELECT name FROM sys.databases WHERE name LIKE '%AX%' OR name LIKE '%Dynamics%';
```

Common values: `MicrosoftDynamicsAX`, `DynamicsAX_PROD`.

### 6. `pyodbc` in the Agent's embedded Python

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\embedded3\python.exe" -c "import pyodbc; print('OK')"
```

If `ModuleNotFoundError`:

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\embedded3\python.exe" -m pip install pyodbc
```

> Installing into the Agent's embedded Python is not officially supported. Agent upgrades may overwrite it — re-run after upgrades.

### 7. Microsoft ODBC Driver 17 or 18 for SQL Server

```powershell
Get-OdbcDriver | Where-Object Name -like '*SQL Server*' | Select-Object Name
```

Install from <https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server> if missing.

### 8. Administrator access on the VM

Right-click Start → **Terminal (Admin)**.

---

## Install

### Step 1 — Place the Python check

Save the file below as:

```
C:\ProgramData\Datadog\checks.d\custom_ax_audit.py
```

Create the folder first if needed:

```powershell
New-Item -ItemType Directory -Force -Path "C:\ProgramData\Datadog\checks.d" | Out-Null
```

<details>
<summary><strong><code>custom_ax_audit.py</code></strong> — click to expand</summary>

```python
# =============================================================================
#  custom_ax_audit.py — Datadog Agent custom check for AX 2012 R3 audit log
#                       ingestion (pyodbc / file-based log output)
# =============================================================================
#  Reads dbo.SYSDATABASELOG every minute, appends each new row as a JSON line
#  to a rotating log file, and persists the last processed RecId via
#  self.write_persistent_cache('last_recid', ...).
#
#  Uses file-based log collection (type: file in conf.yaml) — compatible with
#  all Agent 7.x versions including Agent 7.40-7.47 on Windows Server 2012 R2.
#  self.send_log() is intentionally NOT used; it requires Agent 7.54+ which
#  Windows 2012 R2 cannot run.
#
#  Deployment path:
#    Windows: C:\ProgramData\Datadog\checks.d\custom_ax_audit.py
#    Linux:   /etc/datadog-agent/checks.d/custom_ax_audit.py
#
#  Requirements:
#    * Datadog Agent v7.x (any version).
#    * pyodbc in the Agent's embedded Python.
#    * ODBC Driver 17 or 18 for SQL Server installed on the host.
#    * logs_enabled: true in datadog.yaml.
#    * datadog SQL login with SELECT on dbo.SYSDATABASELOG.
# =============================================================================

import json
import logging
import os
from logging.handlers import RotatingFileHandler

try:
    import pyodbc
    HAS_PYODBC = True
except ImportError:
    HAS_PYODBC = False

from datadog_checks.base import AgentCheck

_DEFAULT_LOG_PATH_WIN = r"C:\ProgramData\Datadog\logs\custom_ax_audit\ax_audit.log"
_DEFAULT_LOG_PATH_NIX = "/var/log/datadog/custom_ax_audit/ax_audit.log"


class CustomAxAuditCheck(AgentCheck):
    """Polls dbo.SYSDATABASELOG and appends new audit rows as JSON lines to a rotating file."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._audit_logger = None

    # Why RotatingFileHandler?
    # self.send_log() (the clean Datadog API) is only available on Agent 7.54+.
    # Windows Server 2012/2012 R2 is capped at Agent 7.47, so we must write to a
    # plain file on disk and let the Agent's built-in file tailer ship the lines.
    #
    # RotatingFileHandler caps the file at maxBytes (20 MB) and keeps backupCount
    # (5) old copies, named ax_audit.log.1 … ax_audit.log.5, so the total disk
    # footprint is bounded at 6 × 20 MB = 120 MB. When the active file hits 20 MB
    # it is renamed to .1, the previous .1 becomes .2, and so on; the oldest .5 is
    # deleted. The Agent only tails ax_audit.log (the active file); the .N backups
    # are already fully shipped before rotation happens.
    def _get_audit_logger(self, log_path):
        if self._audit_logger is not None:
            return self._audit_logger
        log_dir = os.path.dirname(log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        logger = logging.getLogger('custom_ax_audit.file.{}'.format(id(self)))
        logger.setLevel(logging.INFO)
        logger.propagate = False
        handler = RotatingFileHandler(
            log_path,
            maxBytes=20 * 1024 * 1024,  # 20 MB per file
            backupCount=5,
            encoding='utf-8',
        )
        handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(handler)
        self._audit_logger = logger
        return logger

    def check(self, instance):
        if not HAS_PYODBC:
            self.warning(
                "pyodbc not available in the Agent's embedded Python. Install it via: "
                "embedded3\\python.exe -m pip install pyodbc, and ensure an ODBC Driver "
                "for SQL Server is installed on the host."
            )
            return

        host       = instance.get('host')
        username   = instance.get('username')
        password   = instance.get('password')
        database   = instance.get('database')
        driver     = instance.get('driver', 'ODBC Driver 18 for SQL Server')
        extra_conn = instance.get('connection_string', '')

        if not all([host, username, password, database]):
            self.warning("Missing required fields: host, username, password, database.")
            return

        connector = instance.get('connector', 'odbc')
        if connector != 'odbc':
            self.warning("Only connector=odbc is supported.")
            return

        default_log_path = _DEFAULT_LOG_PATH_WIN if os.name == 'nt' else _DEFAULT_LOG_PATH_NIX
        log_path    = instance.get('log_path', default_log_path)
        table_ids   = instance.get('table_ids', [211])
        lag_seconds = int(instance.get('lag_seconds', 30))
        batch_size  = int(instance.get('batch_size', 1000))
        service     = instance.get('service', 'ax-2012')
        ddsource    = instance.get('ddsource', 'ax-audit')
        tags        = list(instance.get('tags', []))

        conn_str = (
            "DRIVER={{{driver}}};"
            "SERVER={host};"
            "DATABASE={database};"
            "UID={username};"
            "PWD={password};"
            "{extra}"
        ).format(
            driver=driver, host=host, database=database,
            username=username, password=password, extra=extra_conn,
        )

        # Read cursor from persistent_cache (survives Agent restarts).
        raw = self.read_persistent_cache('last_recid')
        last_recid = int(json.loads(raw)) if raw else 0
        self.log.info("custom_ax_audit: last_recid from cache = %d", last_recid)

        # Sanitize table_ids to ints (pyodbc IN-list doesn't accept array params).
        sanitized_tables = ','.join(str(int(t)) for t in table_ids)
        sql = (
            "SELECT TOP (?) "
            "    CAST(RecId AS BIGINT)                 AS recid, "
            "    CreatedDateTime                       AS created_at, "
            "    LogType                               AS log_type, "
            "    CAST([Description] AS NVARCHAR(MAX))  AS description, "
            "    [Table]                               AS table_id, "
            "    CAST(LogRecId AS BIGINT)              AS log_recid, "
            "    UserName                              AS user_name "
            "FROM dbo.SYSDATABASELOG "
            "WHERE RecId > ? "
            "  AND CreatedDateTime < DATEADD(SECOND, -?, SYSUTCDATETIME()) "
            "  AND [Table] IN ({tables}) "
            "ORDER BY RecId ASC"
        ).format(tables=sanitized_tables)

        try:
            conn = pyodbc.connect(conn_str)
            cur = conn.cursor()
            cur.execute(sql, batch_size, last_recid, lag_seconds)
            rows = cur.fetchall()
            conn.close()
        except Exception as e:
            self.warning("DB connection / query error: %s", e)
            return

        if not rows:
            self.log.info("custom_ax_audit: no new rows since last check")
            return

        self.log.info("custom_ax_audit: processing %d new rows", len(rows))
        audit_logger = self._get_audit_logger(log_path)
        for row in rows:
            record = {
                'ddsource':   ddsource,
                'service':    service,
                'ddtags':     ','.join(tags) if tags else '',
                'message':    row.description if row.description else '',
                'RecId':      int(row.recid),
                'LogType':    int(row.log_type),
                'TableId':    int(row.table_id),
                'LogRecId':   int(row.log_recid),
                'UserName':   row.user_name if row.user_name else None,
                'created_at': str(row.created_at) if row.created_at else None,
            }
            audit_logger.info(json.dumps(record, default=str))

        # Update cursor ONCE after the whole batch is written.
        self.write_persistent_cache('last_recid', json.dumps(int(rows[-1].recid)))
        self.log.info("custom_ax_audit: updated cursor to RecId=%d", int(rows[-1].recid))
```

</details>

### Step 2 — Place the config

Save the YAML below as:

```
C:\ProgramData\Datadog\conf.d\custom_ax_audit.d\conf.yaml
```

(Folder name `custom_ax_audit.d` must match the Python filename minus `.py`.)

```powershell
New-Item -ItemType Directory -Force -Path "C:\ProgramData\Datadog\conf.d\custom_ax_audit.d" | Out-Null
```

<details>
<summary><strong><code>conf.yaml</code></strong> — click to expand</summary>

```yaml
# =============================================================================
#  custom_ax_audit — Datadog Agent custom check configuration (legacy variant)
# =============================================================================
#  Deployment path:
#    Windows: C:\ProgramData\Datadog\conf.d\custom_ax_audit.d\conf.yaml
#    Linux:   /etc/datadog-agent/conf.d/custom_ax_audit.d/conf.yaml
# =============================================================================

init_config:

instances:

  - host: "localhost,1433"
    username: "datadog"
    password: "<datadog_db_password>"
    database: "MicrosoftDynamicsAX"

    connector: odbc
    driver: "ODBC Driver 18 for SQL Server"

    # Self-signed certs are the default on internal AX SQL Servers.
    # For prod with a trusted CA cert, remove TrustServerCertificate=yes.
    connection_string: "Encrypt=yes;TrustServerCertificate=yes;APP=DD-Agent-AxAudit-Check"

    # AX TableIds to capture. Find IDs via:
    #     SELECT tabId, name FROM <db>.dbo.SqlDictionary WHERE fieldId = 0
    table_ids:
      - 211         # LedgerJournalTable
      # - 210       # LedgerJournalTrans
      # - 77        # CustTable
      # - 78        # VendTable

    lag_seconds: 30
    batch_size: 1000
    min_collection_interval: 60

    service:  ax-2012
    ddsource: ax-audit

    tags:
      - env:prod
      - erp:dynamics-ax
      - ax_version:2012-r3

    # Optional: override the default log output path.
    # Default on Windows: C:\ProgramData\Datadog\logs\custom_ax_audit\ax_audit.log
    # Default on Linux:   /var/log/datadog/custom_ax_audit/ax_audit.log
    # log_path: "C:\\ProgramData\\Datadog\\logs\\custom_ax_audit\\ax_audit.log"

# =============================================================================
# REQUIRED — top-level logs: block (file-based collection).
# The check writes JSON lines to ax_audit.log; this block tells the Agent
# to tail that file and forward each line to Datadog Logs.
# path must match log_path in instances (or the default above).
# =============================================================================
logs:
  - type: file
    path: "C:\\ProgramData\\Datadog\\logs\\custom_ax_audit\\ax_audit.log"
    source: ax-audit
    service: ax-2012
```

</details>

Edit the YAML and update:

- `password:` — the SQL login password from prerequisite 4.
- `database:` — the AX database name from prerequisite 5.
- `driver:` — match an installed ODBC driver (prerequisite 7).
- `table_ids:` — add additional TableIds if needed (default `[211]` = `LedgerJournalTable`).

### Step 3 — Restart the Agent

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" restart-service
```

Wait ~30 seconds for the Agent to come back up.

---

## Verify

### Agent loaded the check

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" status
```

Look for `custom_ax_audit (unversioned)` with `[OK]` status.

### Run the check once interactively

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" check custom_ax_audit --log-level debug
```

Look for `custom_ax_audit: last_recid from cache = N` and either `processing M new rows` or `no new rows since last check`.

### Confirm the log file was created

```powershell
Get-Item "C:\ProgramData\Datadog\logs\custom_ax_audit\ax_audit.log"
Get-Content "C:\ProgramData\Datadog\logs\custom_ax_audit\ax_audit.log" -Tail 5
```

Each line should be a valid JSON object. If the file does not exist after the check runs, confirm the Agent service account has write permission to `C:\ProgramData\Datadog\logs\`.

### Check Datadog Logs Explorer

Open **Logs → Live Tail** and search:

```
service:ax-2012 source:ax-audit
```

Facets available: `@RecId`, `@LogType`, `@TableId`, `@LogRecId`, `@UserName`. The message body is the AX `Description` column.

---

## Operate

### Add a new TableId (config-only — no Python edit)

Edit `conf.yaml`, append to `table_ids:`, restart the Agent.

### Disable temporarily

```powershell
Rename-Item "C:\ProgramData\Datadog\conf.d\custom_ax_audit.d" "custom_ax_audit.d.disabled"
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" restart-service
```

Cursor stays put; re-enabling backfills everything missed during the gap.

### Tail the Agent log

```powershell
Get-Content "$env:ProgramData\Datadog\logs\agent.log" -Wait -Tail 50 |
    Select-String -Pattern 'custom_ax_audit'
```

---

## Common errors

| Error | Cause / fix |
|---|---|
| `'CustomAxAuditCheck' object has no attribute 'send_log'` | You are running the non-legacy `custom_ax_audit.py` on an older Agent. Replace it with the file-based version from this guide and update `conf.yaml` to use `type: file`. |
| Log file not created / no logs in Datadog | `logs_enabled: true` missing in `datadog.yaml`; or `logs:` `path` does not match where the check writes; or the Agent service account lacks write permission to `C:\ProgramData\Datadog\logs\custom_ax_audit\`. |
| `Check 'custom_ax_audit' was not found` | File at wrong path, or folder name doesn't match `custom_ax_audit.d`. |
| `pyodbc not available in the Agent's embedded Python` | Run `embedded3\python.exe -m pip install pyodbc`. |
| `Data source name not found and no default driver specified` | ODBC Driver 17/18 for SQL Server not installed on the host. |
| `Login failed for user 'datadog'` | SQL Server in Windows-auth-only mode — switch to Mixed Mode (prereq 3). |
| `08001 SSL Security error` | TLS failure. Keep `Encrypt=yes;TrustServerCertificate=yes` for self-signed certs, or remove both for unencrypted dev/test. |
| Check loads but no logs in Datadog | `logs_enabled: true` missing; or `logs:` block missing or has wrong `path`; or no new rows in the query window. |

---

## Linux notes

Paths shift to:

- Check: `/etc/datadog-agent/checks.d/custom_ax_audit.py`
- Config: `/etc/datadog-agent/conf.d/custom_ax_audit.d/conf.yaml`
- Log file default: `/var/log/datadog/custom_ax_audit/ax_audit.log`

Update the `logs:` block `path` in `conf.yaml` to match.

Install Microsoft's ODBC Driver 17/18 for Linux, then set `ODBCSYSINI=/etc` as an env var on the Agent service so `pyodbc` finds the driver registration in `/etc/odbcinst.ini`.

In containers: build a custom Agent image with the ODBC driver baked in, and ship `ODBCSYSINI=/etc` via the container env.

---

## References

- [Datadog Agent custom check docs](https://docs.datadoghq.com/developers/custom_checks/)
- [Agent File Log Collection](https://docs.datadoghq.com/logs/log_collection/?tab=host#custom-log-collection)
- [Persistent Cache API](https://datadoghq.dev/integrations-core/base/persistent-cache/)
- [Microsoft ODBC Driver for SQL Server](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server)
- [send_log variant (Agent 7.54+)](./gist-install-query-db-logs-custom-check.md)
