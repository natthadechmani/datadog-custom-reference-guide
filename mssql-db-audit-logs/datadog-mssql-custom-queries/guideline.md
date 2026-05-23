# Datadog SQL Server `custom_queries` — quickstart guideline

A self-contained guide for shipping AX audit logs via the official Datadog
`sqlserver` integration's `custom_queries` feature with `extras: type: log`.

> **Before you start:** complete the common prerequisites in
> [`../prerequisites.md`](../prerequisites.md). This guide covers only the items
> specific to the custom-queries approach.

## Table of contents

1. [What this does](#1-what-this-does)
2. [Approach-specific prerequisites](#2-approach-specific-prerequisites)
3. [Initial setup](#3-initial-setup)
4. [Verify it's working](#4-verify-its-working)
5. [Adding TableIds and columns](#5-adding-tableids-and-columns)
6. [How to view logs and debug](#6-how-to-view-logs-and-debug)
7. [How to restart / disable / remove](#7-how-to-restart--disable--remove)
8. [How the time-window cursor works](#8-how-the-time-window-cursor-works)
9. [Common errors and fixes](#9-common-errors-and-fixes)
10. [FAQ](#10-faq)

---

## 1. What this does

Every minute, the Datadog `sqlserver` integration runs a custom SQL query against
`dbo.SYSDATABASELOG` and emits each result row as a Datadog log event (using the
`columns: type: source` + `extras: type: log` mechanism).

The query uses a **sliding time window** — `WHERE CreatedDateTime >= DATEADD(SECOND,
-60, SYSUTCDATETIME())` — to grab rows from the last 60 seconds on each run. There is
**no persistent cursor**; the check just re-queries the latest window every time.

End result: **config-only deployment, no Python file to maintain, no extra services
on the VM**. Trade-off: a small silent-drop risk if any check is delayed > 60 seconds
(see section 8).

---

## 2. Approach-specific prerequisites

Make sure the [common prerequisites](../prerequisites.md) are in place.

This approach has **no additional prerequisites** beyond the common list. Notably:

- **No pyodbc install needed.** The official `sqlserver` integration ships with the
  Datadog Agent and uses its own bundled SQL connector (adodbapi on Windows, FreeTDS
  on Linux). No `pip install` step.
- **No separate ODBC driver download required on Windows.** adodbapi uses Windows-native
  OLE DB providers (`SQLOLEDB.1` is built into Windows). If you want the newer
  MSOLEDBSQL provider for TLS 1.2+, install it from
  <https://learn.microsoft.com/sql/connect/oledb/download-oledb-driver-for-sql-server>.
- **No code to write.** This entire approach is one YAML file.

That's the main selling point vs. the custom-check approach: fewer moving parts.

---

## 3. Initial setup

The reference file is [`test_configs/conf.yaml`](./test_configs/conf.yaml).

### 3.1 Place the configuration file

This is the **official sqlserver integration's** conf file. The directory and filename
are fixed by Datadog convention:

```
C:\ProgramData\Datadog\conf.d\sqlserver.d\conf.yaml
```

Copy `test_configs/conf.yaml` into that path. If the directory doesn't exist (the
sqlserver integration ships disabled by default):

```powershell
New-Item -ItemType Directory -Force -Path "C:\ProgramData\Datadog\conf.d\sqlserver.d" | Out-Null
Copy-Item "<source-path>\conf.yaml" "C:\ProgramData\Datadog\conf.d\sqlserver.d\conf.yaml"
```

**If a `sqlserver.d\conf.yaml` already exists** (the customer is already collecting SQL
Server metrics), DO NOT overwrite it. Merge instead: copy the `custom_queries:` block
into the existing `instances:` section, and add the top-level `logs:` block at the end.

### 3.2 Edit `conf.yaml`

Update these fields:

- `password:` — replace `<datadog_db_password>` with the real password (or use
  `password: ENC[<key>]` with the secrets backend).
- `MicrosoftDynamicsAX.dbo.SYSDATABASELOG` in the SQL — replace `MicrosoftDynamicsAX`
  with the AX database name from common prereq 5.
- `[Table] IN (211)` — adjust the TableIds you want to capture (section 5.1).

**Important:** the file must have a **top-level `logs:` block** at the very end (NOT
indented under `instances:`):

```yaml
logs:
  - type: integration
    source: sqlserver
    service: ax-2012
```

Without it, the Agent will accept the rows from `custom_queries` log emission but
silently drop them. See section 9.

### 3.3 Restart the Datadog Agent

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" restart-service
```

Wait ~30 seconds for the Agent to come back up.

---

## 4. Verify it's working

### 4.1 Check the Agent loaded the integration

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" status
```

Find the `sqlserver` integration block. Status should be `[OK]` with a recent
`Last Successful Execution Date` and Average Execution Time well under a second.

### 4.2 Run the check once interactively

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" check sqlserver
```

With debug-level for more detail:

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" check sqlserver --log-level debug
```

You should see the `custom_queries` section in the output, with the AX audit rows
fetched by the SELECT.

### 4.3 Check Datadog Logs Explorer

Open **Datadog → Logs → Live Tail** and search:

```
service:ax-2012 source:sqlserver
```

Entries should appear within ~1 minute of the first successful run. Facets:

- `@RecId` (numeric)
- `@LogType` (string: `Insert` / `Update` / `Delete` / `RenameKey`)
- `@TableId`
- `@LogRecId`
- `@UserName`

The message body is the AX `Description` column.

---

## 5. Adding TableIds and columns

Two ways to extend what gets captured: filter additional AX tables (TableIds), or
include additional columns from `dbo.SYSDATABASELOG` in the log payload. Both are
config-only edits to `conf.yaml`.

### 5.1 Add a new TableId

1. Find the TableId via AX's metadata catalog (in SSMS):

   ```sql
   SELECT tabId, name FROM <ax_database>.dbo.SqlDictionary
   WHERE fieldId = 0 AND name IN ('VendTable', 'CustTable');
   ```

2. Edit `C:\ProgramData\Datadog\conf.d\sqlserver.d\conf.yaml`. In the SQL `WHERE`
   clause, extend the `[Table] IN (...)` list:

   ```sql
   AND [Table] IN (211, 78, 77)   -- LedgerJournalTable, VendTable, CustTable
   ```

3. Restart the Agent:

   ```powershell
   & "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" restart-service
   ```

4. Verify in Datadog Logs Explorer:

   ```
   service:ax-2012 @TableId:78
   ```

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
| `dataAreaId` | NVARCHAR(4) | AX legal entity / company code (e.g. `fin`, `usmf`). **Critical for multi-company AX environments**. |
| `Partition` | BIGINT | AX 2012 R3 partition ID. Most environments have one partition. |
| `recVersion` | INT | Record version counter. Rarely useful externally. |

Columns to deliberately **AVOID**:

| Column | Why to skip |
|---|---|
| `Data` | Binary AX `container` blob. Unreadable outside AX X++. May contain field values that shouldn't be exfiltrated. |

#### Step 2: Add the column to the SQL `SELECT`

Edit `C:\ProgramData\Datadog\conf.d\sqlserver.d\conf.yaml`. Add the column to the
SELECT block in `custom_queries[0].query`:

```yaml
custom_queries:
  - query: |
      SELECT TOP (10000)
          RecId                                AS recid,
          CreatedDateTime                      AS created_at,
          CASE LogType WHEN 1 THEN 'Insert' ... END AS log_type,
          CAST([Description] AS NVARCHAR(MAX)) AS description,
          [Table]                              AS table_id,
          LogRecId                             AS log_recid,
          UserName                             AS username,
          dataAreaId                           AS data_area_id    -- NEW
      FROM MicrosoftDynamicsAX.dbo.SYSDATABASELOG
      WHERE CreatedDateTime >= DATEADD(SECOND, -60, SYSUTCDATETIME())
        AND [Table] IN (211)
      ORDER BY recid
```

Rules:

- **Alias to a lowercase name** (e.g. `AS data_area_id`). The integration matches the
  alias against the `columns:` list below.
- **Comma at the end of every column line** except the last one before `FROM`.

#### Step 3: Add the column to the `columns:` list

The `columns:` block declares the result schema and the type (`source` for log fields):

```yaml
    columns:
      - {name: recid,         type: source}
      - {name: created_at,    type: source}
      - {name: log_type,      type: source}
      - {name: description,   type: source}
      - {name: table_id,      type: source}
      - {name: log_recid,     type: source}
      - {name: username,      type: source}
      - {name: data_area_id,  type: source}    # NEW
```

The order must match the order of columns in the SELECT.

#### Step 4: Map the column to a Datadog attribute name

In the `extras.attributes:` block, map the column's `name:` (left side, lowercase) to
the Datadog attribute (right side, PascalCase by convention):

```yaml
    extras:
      - type: log
        attributes:
          RecId:       recid
          LogType:     log_type
          TableId:     table_id
          LogRecId:    log_recid
          UserName:    username
          DataAreaId:  data_area_id    # NEW
          message:     description
          date:        created_at
```

Conventions:

- **PascalCase for the Datadog attribute name** (left side). It will appear as
  `@DataAreaId` in Logs Explorer.
- **Two reserved names:** `message` becomes the log body. `date` becomes the log
  timestamp. Don't use those names for normal data attributes.

#### Step 5: Restart the Agent and verify

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" restart-service
```

Then in Datadog Logs Explorer, the new field appears as a faceted attribute:

```
service:ax-2012 @DataAreaId:fin
```

#### Step 6: (Optional) Promote the new field to a Datadog facet

1. Open any log event in **Logs → Live Tail**.
2. Click the new field's value → **Create facet**.
3. Give it a display name (e.g. "Company Code") and Save.

The field is now available for filtering and aggregation across the whole logs index.

---

## 6. How to view logs and debug

### Tail the Agent's own log

```powershell
Get-Content "$env:ProgramData\Datadog\logs\agent.log" -Wait -Tail 50 |
    Select-String -Pattern 'sqlserver|custom_quer'
```

### Bump verbosity globally

Edit `C:\ProgramData\Datadog\datadog.yaml`:

```yaml
log_level: debug
```

Restart the Agent. Switch back to `info` afterwards — debug is very noisy.

### Run interactively (fastest one-off debugging)

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" check sqlserver --log-level debug
```

Runs the check once in the foreground and prints all log messages and the rows the
`custom_queries` SELECT would emit.

---

## 7. How to restart / disable / remove

### Restart (after editing conf.yaml)

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" restart-service
```

### Disable temporarily

Rename the conf folder so the Agent doesn't load it:

```powershell
Rename-Item "C:\ProgramData\Datadog\conf.d\sqlserver.d" "sqlserver.d.disabled"
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" restart-service
```

Re-enable:

```powershell
Rename-Item "C:\ProgramData\Datadog\conf.d\sqlserver.d.disabled" "sqlserver.d"
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" restart-service
```

While disabled, no audit rows ship to Datadog. The sliding time window means rows that
arrived during downtime longer than 60 seconds are **lost** (not replayed) — see
section 8.

### Disable only the custom_queries part (keep collecting SQL Server metrics)

Edit `conf.yaml` and comment out the `custom_queries:` block AND the top-level
`logs:` block. Keep the rest. Restart the Agent. Metrics still flow; audit log
emission stops.

### Fully remove

If you want to remove the whole sqlserver integration:

```powershell
Remove-Item "C:\ProgramData\Datadog\conf.d\sqlserver.d" -Recurse -Force
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" restart-service
```

---

## 8. How the time-window cursor works

### The pattern

Unlike the [custom-check](../datadog-custom-check/guideline.md) approach which uses
a persistent RECID cursor, the `custom_queries` approach uses a **sliding time
window** built into the SQL `WHERE` clause:

```sql
WHERE CreatedDateTime >= DATEADD(SECOND, -60, SYSUTCDATETIME())
```

Translation: "Return rows whose CreatedDateTime is within the last 60 seconds."

The check fires every 60 seconds (`min_collection_interval: 60`). If everything is
on time and the windows are perfectly contiguous, every audit row is captured exactly
once.

### The silent-drop risk

There is no persistent state between runs. So:

| Scenario | Result |
|---|---|
| Check delayed by 30s due to GC pause | Window still covers the last 60s. Some rows captured twice (duplicates). |
| Check delayed by 90s due to Agent restart | Window jumps forward. **Rows from the 30s gap are silently dropped.** |
| Agent off for 10 minutes | When it comes back, only the last 60s of rows are captured. **9+ minutes of rows are silently lost.** |
| SQL query takes 70s to run | Window slides past some rows. **Drops possible.** |

For audit data where occasional missing rows are acceptable, this is fine. For data
where every row must be captured, use the
[custom-check approach](../datadog-custom-check/guideline.md) which has a persistent
RECID cursor.

### Widening the window for more tolerance

You can buy drop tolerance with duplicate cost. Edit both lines together:

```yaml
min_collection_interval: 60   # check runs every 60s
```

```sql
WHERE CreatedDateTime >= DATEADD(SECOND, -75, SYSUTCDATETIME())   -- 15s overlap
```

- `60s / -75s` → 15s overlap, ~1.25× duplicate ingestion, survives 15s delays.
- `60s / -90s` → 30s overlap, ~1.5× duplicate ingestion, survives 30s delays.
- `60s / -120s` → 60s overlap, 2× duplicate ingestion, survives a full missed run.

Each row that survives the overlap is shipped multiple times. You can dedupe in
Datadog using `@RecId` as the unique key.

---

## 9. Common errors and fixes

### "Failed to write log to file, file is nil for integration ID: sqlserver:..."

Visible in `agent check sqlserver --log-level debug`. The integration is producing
log events but the Agent has no integration log file to write them to because
conf.yaml is missing the top-level `logs:` block.

**Fix:** add to the end of conf.yaml (top level, NOT indented under `instances:`):

```yaml
logs:
  - type: integration
    source: sqlserver
    service: ax-2012
```

Restart the Agent. To recover dropped rows, you can't — the time-window approach has
no replay. Wait for new rows to arrive and ship.

### "Login failed for user 'datadog'"

SQL Server is in Windows-auth-only mode. Switch to Mixed Mode and restart SQL Server
(common prereq 3).

### "DB connection / query error: ... SSL Security error"

TLS handshake failure between adodbapi and SQL Server. The default OLE DB provider
`SQLOLEDB.1` only supports old TLS protocols. Two fixes:

- Drop `Encrypt=...` and `TrustServerCertificate=...` from `connection_string` for
  dev/test (loopback traffic is fine unencrypted).
- Install the newer `MSOLEDBSQL` OLE DB driver and switch the connection_string to
  use it (see Microsoft docs).

### Check loads but no logs appear in Datadog

- `logs_enabled: true` missing from `datadog.yaml`. Set it and restart the Agent.
- conf.yaml is missing the top-level `logs:` block — see the first error in this section.
- Verify rows exist in the time window:

  ```sql
  SELECT COUNT(*) FROM dbo.SYSDATABASELOG
  WHERE [Table] IN (211)
    AND CreatedDateTime >= DATEADD(SECOND, -60, SYSUTCDATETIME());
  ```

### `(unversioned)` showing next to the check

Cosmetic only — the official `sqlserver` integration shows its actual version (e.g.
`sqlserver (23.0.1)`). This won't be `unversioned`. If yours is unversioned you're
probably looking at a custom check instead.

### Rows are being dropped — confirmed via RecId gap

The time-window approach can't replay. Three options:

- Widen the window per section 8.
- Increase `min_collection_interval` to reduce check overhead.
- **Switch to the custom-check approach** in `../datadog-custom-check/` which has a
  durable RECID cursor.

---

## 10. FAQ

### Q: How is this different from the custom-check approach?

This approach is **config-only** (one YAML file). The custom-check approach is
**code + config** (a Python file + a conf.yaml).

| | This (`custom_queries`) | Custom check |
|---|---|---|
| Files to maintain | 1 (`conf.yaml`) | 2 (`.py` + `conf.yaml`) |
| Watermark | Sliding 60s time window | Persistent RECID cursor |
| Drop risk | Yes (silent, on delays > 60s) | No (cursor replays) |
| Install footprint | None — uses bundled adodbapi | Needs pyodbc + ODBC Driver 17/18 |
| Adding a field | Edit YAML in 3 places | Edit Python in 2 places |
| LogType to text mapping | Done in SQL `CASE` | Could be done in Python or Datadog pipeline |

### Q: Why is `LogType` mapped to a string in this approach but kept as int in the custom check?

Personal taste / consistency with the AX UI. Both can be done either way. In the
custom check the mapping was deliberately left in Python (or omitted) to keep the
check minimal. In this YAML approach, doing it in SQL is just as easy.

If you want `LogType` as int instead of a string, remove the `CASE` in the SQL and
just `SELECT LogType AS log_type` — the rest of the conf.yaml stays the same.

### Q: Does this require DBM (Database Monitoring)?

No. `custom_queries` with `extras: type: log` works in the plain `sqlserver`
integration without enabling `dbm: true`. The DBM paid SKU is NOT required for this
feature.

### Q: Can I run this alongside the custom-check approach?

Yes, but you'd ingest the same audit data twice. If you're trialing both:

- Use a different `service:` tag in each conf.yaml during the trial (e.g.
  `ax-2012-customquery` vs `ax-2012-customcheck`).
- Pick the one you want for production and remove the other's `logs:` block (or the
  whole custom_queries block).

### Q: Can the integration run at sub-60-second intervals?

Yes — set `min_collection_interval: 15` to poll every 15s. Adjust the
`DATEADD(SECOND, -X, ...)` lookback to match (`-15` for no overlap, `-20` for 5s of
overlap, etc.).

### Q: Does this work on Linux?

Yes. The sqlserver integration uses FreeTDS via odbc on Linux. You'd need to set
`connector: odbc` and `driver: FreeTDS` (or `ODBC Driver 18 for SQL Server` if
you've installed it) in the instance config. Otherwise the YAML is the same.

### Q: How do I see what columns the integration returns at runtime?

Run interactively:

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" check sqlserver --log-level debug
```

The debug output includes the rows fetched and the log events emitted. Useful for
confirming a new column shows up.

### Q: Why does `min_collection_interval` need to match the SQL `DATEADD` window?

The window in the SQL is what each query reads. The interval is how often the query
runs. If they don't match, you get either silent drops (window < interval) or
exponentially growing duplicates (window >> interval). Always change them together,
in the same conf.yaml save.
