# Datadog Custom Check — quickstart guideline

A self-contained guide for deploying the `custom_ax_audit` Python check on a Windows
host.

> **Before you start:** complete the common prerequisites in
> [`../prerequisites.md`](../prerequisites.md). This guide covers only the items
> specific to the custom-check approach (pyodbc + ODBC driver).

## Table of contents

1. [What this does](#1-what-this-does)
2. [Approach-specific prerequisites](#2-approach-specific-prerequisites)
3. [Initial setup](#3-initial-setup)
4. [Verify it's working](#4-verify-its-working)
5. [Adding TableIds and columns](#5-adding-tableids-and-columns)
6. [How to view logs and debug](#6-how-to-view-logs-and-debug)
7. [How to restart / disable / remove](#7-how-to-restart--disable--remove)
8. [How the persistent_cache cursor works](#8-how-the-persistent_cache-cursor-works)
9. [Common errors and fixes](#9-common-errors-and-fixes)
10. [FAQ](#10-faq)

---

## 1. What this does

Every minute, the check queries `dbo.SYSDATABASELOG` for new audit rows
(`RECID > last processed`) and ships each row as a Datadog log via
`self.send_log()`. The last processed RECID is stored in the Agent's
`persistent_cache` (key: `last_recid`) so it survives Agent restarts.

End result: durable cursor, no silent drops from time-window gaps, no separate
scheduled task, no `.jsonl` files on disk. Just two files (Python check + conf.yaml)
inside the Datadog Agent's standard layout.

---

## 2. Approach-specific prerequisites

Make sure the [common prerequisites](../prerequisites.md) are in place, plus:

### 2.1 `pyodbc` available in the Agent's embedded Python

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\embedded3\python.exe" -c "import pyodbc; print('OK')"
```

Should print `OK`. If you get `ModuleNotFoundError`:

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\embedded3\python.exe" -m pip install pyodbc
```

(Re-run the import check to confirm.)

Note: installing third-party packages into the Agent's embedded Python is not officially
supported by Datadog. Agent upgrades may overwrite the install.

### 2.2 ODBC Driver 17 or 18 for SQL Server

```powershell
Get-OdbcDriver | Where-Object Name -like '*SQL Server*' | Select-Object Name
```

Or open `odbcad32.exe` → **Drivers** tab and look for
`ODBC Driver 17 for SQL Server` or `ODBC Driver 18 for SQL Server`.

If missing, install Driver 18 (newer, recommended) from:
<https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server>

---

## 3. Initial setup

The reference files for this approach are in [`test_configs/`](./test_configs/):

- [`test_configs/custom_ax_audit.py`](./test_configs/custom_ax_audit.py) — the Python check
- [`test_configs/conf.yaml`](./test_configs/conf.yaml) — the check's configuration

### 3.1 Place the Python check file

Copy `test_configs/custom_ax_audit.py` to:

```
C:\ProgramData\Datadog\checks.d\custom_ax_audit.py
```

If the folder doesn't exist:

```powershell
New-Item -ItemType Directory -Force -Path "C:\ProgramData\Datadog\checks.d" | Out-Null
```

### 3.2 Place the configuration file

The conf folder name must be `custom_ax_audit.d` (the Python filename minus `.py`).

```powershell
New-Item -ItemType Directory -Force -Path "C:\ProgramData\Datadog\conf.d\custom_ax_audit.d" | Out-Null
Copy-Item "<source-path>\conf.yaml" "C:\ProgramData\Datadog\conf.d\custom_ax_audit.d\conf.yaml"
```

Edit `conf.yaml`:

- Update `password:` to the SQL login password from common prereq 4.
- Confirm `database:` matches the name from common prereq 5.
- Confirm `driver:` matches an installed ODBC driver (section 2.2 above).
- Adjust `table_ids:` if needed (default `[211]` = `LedgerJournalTable`).

**Important:** conf.yaml must have a **top-level `logs:` block** (NOT indented under
`instances:`):

```yaml
logs:
  - type: integration
    source: ax-audit
    service: ax-2012
```

Without it, `self.send_log()` calls are silently dropped. See section 9.

### 3.3 Restart the Datadog Agent

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" restart-service
```

Wait ~30 seconds for the Agent to come back up.

---

## 4. Verify it's working

### 4.1 Check the Agent loaded the check

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" status
```

Find the `custom_ax_audit (unversioned)` section. Status should be `[OK]` with a recent
`Last Successful Execution Date`.

### 4.2 Run the check once interactively

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" check custom_ax_audit
```

With debug-level for more detail:

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" check custom_ax_audit --log-level debug
```

Look for `custom_ax_audit: last_recid from cache = N` and either
`processing M new rows` or `no new rows since last check`.

### 4.3 Check Datadog Logs Explorer

Open **Datadog → Logs → Live Tail** and search:

```
service:ax-2012 source:ax-audit
```

Entries should appear within ~1 minute of the first successful run. Facets:

- `@RecId` (numeric)
- `@LogType` (raw integer: 1, 2, 4, 8)
- `@TableId`
- `@LogRecId`
- `@UserName`

The message body is the AX `Description` column.

---

## 5. Adding TableIds and columns

Two ways to extend what gets captured: filter additional AX tables (TableIds), or
include additional columns from `dbo.SYSDATABASELOG` in the log payload.

### 5.1 Add a new TableId (config-only)

1. Find the TableId via AX's metadata catalog (in SSMS):

   ```sql
   SELECT tabId, name FROM <ax_database>.dbo.SqlDictionary
   WHERE fieldId = 0 AND name IN ('VendTable', 'CustTable');
   ```

2. Edit `C:\ProgramData\Datadog\conf.d\custom_ax_audit.d\conf.yaml`:

   ```yaml
   table_ids:
     - 211   # LedgerJournalTable
     - 78    # VendTable
     - 77    # CustTable
   ```

3. Restart the Agent:

   ```powershell
   & "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" restart-service
   ```

4. Verify in Datadog Logs Explorer:

   ```
   service:ax-2012 @TableId:78
   ```

You don't need to touch `custom_ax_audit.py` — only conf.yaml changes.

### 5.2 Add a new column/field from `dbo.SYSDATABASELOG`

The check currently ships these fields per row: `RecId`, `CreatedDateTime`, `LogType`,
`Description`, `Table`, `LogRecId`, `UserName`. `SYSDATABASELOG` has other columns
you might want included.

#### Step 1: List all columns in SYSDATABASELOG

In SSMS, against the AX database:

```sql
SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'SYSDATABASELOG'
ORDER BY ORDINAL_POSITION;
```

Columns NOT currently captured that are commonly useful:

| Column | Type | Why it might matter |
|---|---|---|
| `dataAreaId` | NVARCHAR(4) | AX legal entity / company code (e.g. `fin`, `usmf`). **Critical for multi-company AX environments** — without it, you can't tell which legal entity an audit row belongs to. |
| `Partition` | BIGINT | AX 2012 R3 partition ID. Most environments have one partition, but if the customer uses partitioning this becomes relevant. |
| `recVersion` | INT | Record version counter on the audit row itself. Rarely useful externally. |

Columns to deliberately **AVOID**:

| Column | Why to skip |
|---|---|
| `Data` | Binary blob (an AX `container` serialized to varbinary). Unreadable outside AX X++. Don't ship it — produces noise in Datadog and may contain field values that shouldn't be exfiltrated. |

#### Step 2: Update the SQL in `custom_ax_audit.py`

Edit `C:\ProgramData\Datadog\checks.d\custom_ax_audit.py`. Find the `sql = (...)`
block and add the new column to the `SELECT`. Example — adding `dataAreaId`:

```python
sql = (
    f"SELECT TOP (?) "
    f"    CAST(RecId AS BIGINT)                 AS recid, "
    f"    CreatedDateTime                       AS created_at, "
    f"    LogType                               AS log_type, "
    f"    CAST([Description] AS NVARCHAR(MAX))  AS description, "
    f"    [Table]                               AS table_id, "
    f"    CAST(LogRecId AS BIGINT)              AS log_recid, "
    f"    UserName                              AS user_name, "
    f"    dataAreaId                            AS data_area_id "      # NEW
    f"FROM dbo.SYSDATABASELOG "
    f"WHERE RecId > ? "
    f"  AND CreatedDateTime < DATEADD(SECOND, -?, SYSUTCDATETIME()) "
    f"  AND [Table] IN ({sanitized_tables}) "
    f"ORDER BY RecId ASC"
)
```

Rules:

- **Alias to a lowercase name** (e.g. `AS data_area_id`). pyodbc exposes columns as
  attributes (`row.data_area_id`) — they have to be valid Python identifiers.
- **Comma at the end of every column line** except the last one before `FROM`.
- **Cast to a known type** with `CAST()` if the column has a tricky type
  (`NVARCHAR(MAX)`, `BIGINT`, etc.).

#### Step 3: Update the `send_log()` payload

Find the `for row in rows:` loop and add the new field to the dict:

```python
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
        'DataAreaId': row.data_area_id if row.data_area_id else None,   # NEW
        'created_at': str(row.created_at) if row.created_at else None,
    })
```

Conventions:

- **PascalCase for the attribute name** (matches existing `RecId`, `LogType`, etc.).
  Datadog auto-prefixes it with `@` in Logs Explorer.
- **Handle NULLs** with the `if row.col else None` pattern so empty values don't
  ship as the literal string `"None"`.
- **Numeric columns**: cast with `int(...)` or `float(...)`.
- **Datetime columns**: use `str(row.col) if row.col else None` (or convert to
  epoch ms if you want Datadog to treat it as a timestamp).

#### Step 4: Restart the Agent and verify

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" restart-service
```

Then in Datadog Logs Explorer, the new field appears as a faceted attribute:

```
service:ax-2012 @DataAreaId:fin
```

#### Step 5: (Optional) Promote the new field to a Datadog facet

By default, new attributes show up under "Attributes" in a log event's details but
aren't pre-indexed for fast filtering. To make `@DataAreaId` (or whatever you added)
a first-class facet:

1. Open any log event in **Logs → Live Tail**.
2. Click the new field's value → **Create facet**.
3. Give it a display name (e.g. "Company Code") and Save.

The field is now available for filtering and aggregation across the whole logs
index.

---

## 6. How to view logs and debug

### Tail the Agent's own log

```powershell
Get-Content "$env:ProgramData\Datadog\logs\agent.log" -Wait -Tail 50 |
    Select-String -Pattern 'custom_ax_audit'
```

### Bump verbosity globally

Edit `C:\ProgramData\Datadog\datadog.yaml`:

```yaml
log_level: debug
```

Restart the Agent. Switch back to `info` afterwards — debug is very noisy.

### Run interactively (fastest one-off debugging)

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" check custom_ax_audit --log-level debug
```

Runs the check once in the foreground and prints all log messages from the script.

---

## 7. How to restart / disable / remove

### Restart (after editing the script or config)

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" restart-service
```

The cursor stays persisted, so the next run picks up where the previous one left off.

### Disable temporarily

Rename the conf folder so the Agent doesn't load it:

```powershell
Rename-Item "C:\ProgramData\Datadog\conf.d\custom_ax_audit.d" "custom_ax_audit.d.disabled"
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" restart-service
```

Re-enable:

```powershell
Rename-Item "C:\ProgramData\Datadog\conf.d\custom_ax_audit.d.disabled" "custom_ax_audit.d"
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" restart-service
```

While disabled, the cursor stays put. When you re-enable, the check picks up all rows
that arrived during the downtime.

### Fully remove

```powershell
Remove-Item "C:\ProgramData\Datadog\conf.d\custom_ax_audit.d" -Recurse -Force
Remove-Item "C:\ProgramData\Datadog\checks.d\custom_ax_audit.py"
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" restart-service
```

The cursor file at `C:\ProgramData\Datadog\run\custom_ax_audit\last_recid` lingers but
is harmless.

---

## 8. How the `persistent_cache` cursor works

Persistent Cache API
https://datadoghq.dev/integrations-core/base/persistent-cache/

send_log Function
https://datadoghq.dev/integrations-core/base/api/#datadog_checks.base.checks.base.AgentCheck.send_log

### The pattern

```python
# Read the last RECID we processed
raw = self.read_persistent_cache('last_recid')
last_recid = int(json.loads(raw)) if raw else 0

# ... query for RecId > last_recid ...

# Ship every row first
for row in rows:
    self.send_log({ ...the log data... })

# Then update the cursor ONCE, after the whole batch
self.write_persistent_cache('last_recid', json.dumps(int(rows[-1].recid)))
```

### Where the cursor is stored

```
C:\ProgramData\Datadog\run\custom_ax_audit\last_recid
```

Plain text file (JSON-encoded integer). Editable when the Agent is stopped.

### Resetting the cursor

To re-process audit rows from a specific RecId:

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" stop-service

# Replace "0" with the RecId you want to resume from.
Set-Content -Path 'C:\ProgramData\Datadog\run\custom_ax_audit\last_recid' `
            -Value '0' -NoNewline

& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" start-service
```

A value of `0` re-processes everything from the start of `SYSDATABASELOG` — careful
with ingestion cost on a real environment.

---

## 9. Common errors and fixes

### "Failed to write log to file, file is nil for integration ID: custom_ax_audit:..."

Visible in `agent check custom_ax_audit --log-level debug`, interleaved with
`custom_ax_audit: updated cursor to RecId=...` INFO lines.

**What's happening:** `self.send_log()` calls reach the Agent but it has no integration
log file to write them to because conf.yaml is missing a top-level `logs:` block.
Cursor advances normally → rows are silently dropped.

**Fix:** add to the end of conf.yaml (top level, NOT indented under `instances:`):

```yaml
logs:
  - type: integration
    source: ax-audit
    service: ax-2012
```

Restart the Agent. To recover dropped rows, reset the cursor (section 8) to a known-
good RecId.

### "Check 'custom_ax_audit' was not found"

The Python file isn't at the right path or the filename doesn't match the conf folder.

- File at `C:\ProgramData\Datadog\checks.d\custom_ax_audit.py`
- Folder `C:\ProgramData\Datadog\conf.d\custom_ax_audit.d\` (must end in `.d`)
- conf.yaml inside that folder

### "pyodbc not available in the Agent's embedded Python..."

Install pyodbc into the Agent's bundled Python:

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\embedded3\python.exe" -m pip install pyodbc
```

### "Data source name not found and no default driver specified"

The ODBC Driver for SQL Server isn't installed on the host. Install from Microsoft
(see section 2.2).

### "Login failed for user 'datadog'"

SQL Server is in Windows-auth-only mode. Switch to Mixed Mode and restart SQL Server
(see common prereq 3).

### "DB connection / query error: 08001"

Generic connectivity error. Verify the `host:` in conf.yaml matches the actual SQL
Server hostname/instance and port (default 1433).

### "DB connection / query error: ... SSL Security error"

TLS handshake failure. With ODBC Driver 18, this can happen if the SQL Server's cert
isn't trusted. Either:

- Install Driver 18 and keep `Encrypt=yes;TrustServerCertificate=yes` in
  `connection_string` (works against self-signed certs).
- Or remove `Encrypt=yes;TrustServerCertificate=yes` for dev/test where unencrypted
  loopback traffic is acceptable.

### Check loads but no logs appear in Datadog

- `logs_enabled: true` missing from `datadog.yaml`. Set it and restart the Agent.
- conf.yaml is missing the top-level `logs:` block — see the first error in this section.
- Verify rows exist in the watermark window:

  ```sql
  SELECT COUNT(*) FROM dbo.SYSDATABASELOG
  WHERE [Table] IN (211) AND RecId > <current-cursor-value>;
  ```

### Cursor never advances (`last_recid` stays at the same value)

- Check `send_log` is succeeding — look in `agent.log` for log-pipeline errors.
- Verify the SQL returns rows when run manually as the `datadog` user via SSMS.
- The check might quietly be returning (no new rows in window) — look for the
  `no new rows since last check` message in the log.

### `agent check custom_ax_audit` shows no output

Normal if there are no new rows in the time window. To force a fresh query, reset the
cursor (section 8) so it picks up older rows.

---

## 10. FAQ

### Q: Why is this simpler than the DBM-style `extras: type: log` approach?

The DBM approach uses a sliding time window with no persistent cursor. If the Agent
restarts or the check is delayed > 60 seconds, rows are silently dropped. This custom
check uses a `persistent_cache`-backed RECID watermark — restarts and delays just mean
the next run catches up.

### Q: Why not map `LogType` int → "Insert"/"Update" string like the AX UI shows?

To keep the check minimal. Datadog can do the mapping in a logs pipeline processor at
the platform side — that way the mapping is data-only (no code) and easy to extend
without redeploying the check. See
<https://docs.datadoghq.com/logs/log_configuration/processors/>.

If you'd prefer the mapping in the check (a small Python dict + lookup), it's a 5-line
edit to `custom_ax_audit.py`.

### Q: Does this replace the sqlserver integration?

No. The `sqlserver` integration still runs for collecting SQL Server metrics. This
`custom_ax_audit` custom check is SEPARATE, with its own conf file and Python file.

You should, however, REMOVE the `custom_queries` + `extras: type: log` block from any
existing `sqlserver.d/conf.yaml` so you don't ingest the same audit data twice.

### Q: Can the Agent run this check at sub-60-second intervals?

Yes. Set `min_collection_interval: 15` in conf.yaml for 15-second polling. The cursor
approach doesn't care about frequency — fewer rows per run, more frequent shipping.

### Q: Does this work on Linux?

Yes. The check is plain Python with pyodbc — no Windows-specific APIs. On Linux:

- Place files under `/etc/datadog-agent/checks.d/custom_ax_audit.py` and
  `/etc/datadog-agent/conf.d/custom_ax_audit.d/conf.yaml`.
- Install Microsoft's ODBC Driver 17 or 18 for SQL Server on Linux.
- Set `ODBCSYSINI=/etc` env var for the Agent service if you hit driver-resolution issues.

### Q: What happens if the check throws an exception?

The Agent records the run as failed. The cursor stays at its previous value (the
exception happens before `write_persistent_cache` is called at the end of the batch).
The next scheduled run retries from the same point.

### Q: Why does the connection string use `Encrypt=yes;TrustServerCertificate=yes`?

`Encrypt=yes` requests TLS encryption between the Agent and SQL Server.
`TrustServerCertificate=yes` skips validating the server's TLS certificate — required
if SQL Server has a self-signed cert (typical for internal AX SQL Servers). For
production with a properly-issued cert from a trusted CA, REMOVE
`TrustServerCertificate=yes` for stricter security.

### Q: Will updates to custom_ax_audit.py be applied automatically?

After you replace the file at `C:\ProgramData\Datadog\checks.d\custom_ax_audit.py`, the
Agent detects the change on the next check run. For safety, restart the Agent after
any code change:

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" restart-service
```
