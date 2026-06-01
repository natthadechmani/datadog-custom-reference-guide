# Datadog Custom Check — AX 2012 R3 Query audit log from SQL Server ingestion

> Install guide for the `custom_ax_audit` Datadog Agent custom check.
> Ships rows from `dbo.SYSDATABASELOG` to Datadog Logs with a persistent `RecId`
> cursor — survives Agent restarts, no silent drops.
>
> Source of truth: <https://github.com/your-org/datadog-custom-reference-guide/tree/main/mssql-db-audit-logs/datadog-custom-check>

> **There is another option, but not recommended for audit logs.** With datadog's
> `sqlserver` integration's [`custom_queries` feature](https://github.com/DataDog/integrations-core/blob/master/sqlserver/datadog_checks/sqlserver/data/conf.yaml.example)
> [`datadog-mssql-custom-queries/guideline.md`](./datadog-mssql-custom-queries/guideline.md)
> for that approach. Use it only for dev / best-effort scenarios, not compliance use cases.

---

## What you get

- One Python check + one YAML config running inside the Datadog Agent.
- Every minute: `SELECT WHERE RecId > <last>` from `SYSDATABASELOG`, ships each row via `self.send_log()`.
- Cursor stored in the Agent's `persistent_cache` → durable across restarts.
- Logs land in Datadog under `service:ax-2012 source:ax-audit` with facets `@RecId`, `@LogType`, `@TableId`, `@UserName`, `@LogRecId`.

---

## Architecture

```
   SQL Server                      Datadog Agent (on the VM)                  Datadog
 ┌─────────────┐               ┌─────────────────────────────┐            ┌───────────┐
 │             │               │                             │            │           │
 │ SYSDATABASE │  ── query ──▶ │  custom_ax_audit.py         │ ── TLS ──▶ │  Logs     │
 │   LOG       │   (pyodbc)    │   (every 60s)               │   443      │  intake   │
 │             │               │                             │            │           │
 └─────────────┘               └──────┬───────────────┬──────┘            └───────────┘
                                      │ reads         │ reads / writes
                                      ▼               ▼
                                 conf.yaml        last_recid
                                 (settings)       (cursor file)
```

Every 60 seconds the check:

1. Reads the cursor from `last_recid` (persistent_cache).
2. Queries SQL Server for rows `WHERE RecId > <cursor>`.
3. Ships each row to Datadog via `self.send_log()`.
4. Writes the new cursor back to `last_recid`.

### Files

| File | Purpose |
|---|---|
| `checks.d\custom_ax_audit.py` | The check code. |
| `conf.d\custom_ax_audit.d\conf.yaml` | DB creds, table_ids, `logs:` block. |
| `run\custom_ax_audit\last_recid` | The persistent cursor (JSON int). |

---

## Prerequisites

### 1. Datadog Agent v7.21+

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" version
```

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
#                       ingestion (pyodbc / cross-platform variant)
# =============================================================================
#  Reads dbo.SYSDATABASELOG every minute, ships each new row as a Datadog log
#  via self.send_log(), and persists the last processed RecId via
#  self.write_persistent_cache('last_recid', ...).
#
#  Deployment path:
#    Windows: C:\ProgramData\Datadog\checks.d\custom_ax_audit.py
#    Linux:   /etc/datadog-agent/checks.d/custom_ax_audit.py
#
#  Requirements:
#    * Datadog Agent v7.21+ (send_log + persistent_cache API).
#    * pyodbc in the Agent's embedded Python.
#    * ODBC Driver 17 or 18 for SQL Server installed on the host.
#    * logs_enabled: true in datadog.yaml.
#    * datadog SQL login with SELECT on dbo.SYSDATABASELOG.
# =============================================================================

import json

try:
    import pyodbc
    HAS_PYODBC = True
except ImportError:
    HAS_PYODBC = False

from datadog_checks.base import AgentCheck


class CustomAxAuditCheck(AgentCheck):
    """Polls dbo.SYSDATABASELOG and ships new audit rows to Datadog Logs."""

    def check(self, instance):
        if not HAS_PYODBC:
            self.warning(
                "pyodbc not available in the Agent's embedded Python. Install it via: "
                "embedded3\\python.exe -m pip install pyodbc, and ensure an ODBC Driver "
                "for SQL Server is installed on the host."
            )
            return

        host     = instance.get('host')
        username = instance.get('username')
        password = instance.get('password')
        database = instance.get('database')
        driver   = instance.get('driver', 'ODBC Driver 18 for SQL Server')
        extra_conn = instance.get('connection_string', '')

        if not all([host, username, password, database]):
            self.warning("Missing required fields: host, username, password, database.")
            return

        connector = instance.get('connector', 'odbc')
        if connector != 'odbc':
            self.warning("Only connector=odbc is supported.")
            return

        table_ids   = instance.get('table_ids', [211])
        lag_seconds = int(instance.get('lag_seconds', 30))
        batch_size  = int(instance.get('batch_size', 1000))
        service     = instance.get('service', 'ax-2012')
        ddsource    = instance.get('ddsource', 'ax-audit')
        tags        = list(instance.get('tags', []))

        conn_str = (
            f"DRIVER={{{driver}}};"
            f"SERVER={host};"
            f"DATABASE={database};"
            f"UID={username};"
            f"PWD={password};"
            f"{extra_conn}"
        )

        # Read cursor from persistent_cache (survives Agent restarts).
        raw = self.read_persistent_cache('last_recid')
        last_recid = int(json.loads(raw)) if raw else 0
        self.log.info("custom_ax_audit: last_recid from cache = %d", last_recid)

        # Sanitize table_ids to ints (pyodbc IN-list doesn't accept array params).
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

        # Update cursor ONCE after the whole batch is shipped.
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
#  custom_ax_audit — Datadog Agent custom check configuration
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

# =============================================================================
# REQUIRED — top-level logs: block.
# Without it, self.send_log() calls are silently dropped with:
#   "Failed to write log to file, file is nil for integration ID: custom_ax_audit:..."
# =============================================================================
logs:
  - type: integration
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
| `Failed to write log to file, file is nil for integration ID: custom_ax_audit:...` | Missing top-level `logs:` block in `conf.yaml`. Cursor advances but rows are dropped. Add the block, restart, reset the cursor to recover. |
| `Check 'custom_ax_audit' was not found` | File at wrong path, or folder name doesn't match `custom_ax_audit.d`. |
| `pyodbc not available in the Agent's embedded Python` | Run `embedded3\python.exe -m pip install pyodbc`. |
| `Data source name not found and no default driver specified` | ODBC Driver 17/18 for SQL Server not installed on the host. |
| `Login failed for user 'datadog'` | SQL Server in Windows-auth-only mode — switch to Mixed Mode (prereq 3). |
| `08001 SSL Security error` | TLS failure. Keep `Encrypt=yes;TrustServerCertificate=yes` for self-signed certs, or remove both for unencrypted dev/test. |
| Check loads but no logs in Datadog | `logs_enabled: true` missing; or top-level `logs:` block missing; or no new rows in window. |

---

## Linux notes

Paths shift to:

- Check: `/etc/datadog-agent/checks.d/custom_ax_audit.py`
- Config: `/etc/datadog-agent/conf.d/custom_ax_audit.d/conf.yaml`

Install Microsoft's ODBC Driver 17/18 for Linux, then set `ODBCSYSINI=/etc` as an env var on the Agent service so `pyodbc` finds the driver registration in `/etc/odbcinst.ini`.

In containers: build a custom Agent image with the ODBC driver baked in, and ship `ODBCSYSINI=/etc` via the container env.

---

## References

- [Datadog Agent custom check docs](https://docs.datadoghq.com/developers/custom_checks/)
- [Agent Integration Log Collection](https://docs.datadoghq.com/logs/log_collection/agent_checks/)
- [Persistent Cache API](https://datadoghq.dev/integrations-core/base/persistent-cache/)
- [`send_log` API](https://datadoghq.dev/integrations-core/base/api/#datadog_checks.base.checks.base.AgentCheck.send_log)
- [Microsoft ODBC Driver for SQL Server](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server)
