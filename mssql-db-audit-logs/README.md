# MS SQL Server → Datadog Logs — AX 2012 R3 audit log pipelines

> Part of the [Datadog Custom Reference Guide](../README.md) — reference implementations

Datadog does not ship a first-party integration for Microsoft Dynamics AX 2012 R3 database
audit tables. This guide documents two Agent-side patterns to ingest `dbo.SYSDATABASELOG`
rows into Datadog Logs. Both produce **the same logs** with identical facets
(`@RecId`, `@LogType`, `@TableId`, `@UserName`, ...). The difference is in the producer:
how rows are read, how the "last processed" position is tracked, and how much code vs.
config you maintain.

| Approach | Pattern | Official reference |
|---|---|---|
| A. Custom check | Agent-based integration (custom Python check + `send_log`) | [Agent Integration Log Collection](https://docs.datadoghq.com/logs/log_collection/agent_checks/) · [Create an Agent-based Integration](https://docs.datadoghq.com/extend/integrations/agent_integration/?tab=ootbintegration) |
| B. `custom_queries` | Core `sqlserver` integration feature in [integrations-core](https://github.com/DataDog/integrations-core) | [`sqlserver` conf.yaml example](https://github.com/DataDog/integrations-core/blob/master/sqlserver/datadog_checks/sqlserver/data/conf.yaml.example) |

---

## Recommendation: Approach A (custom check) for audit logs

For audit log collection, **use Approach A (custom check with `persistent_cache`).**

Approach B can be easier to install, but it has no persistent cursor — any Agent
restart, GC pause, or check delay > 60s silently drops audit rows. That makes B unsuitable
for compliance / audit use cases. Use B only for dev or best-effort scenarios.

| | A. Custom check (recommended) | B. `custom_queries` |
|---|---|---|
| **Audit-grade delivery** | Yes — `RecId` cursor, no data loss | No — sliding window drops on restart |
| **Ease of installation** | Medium — needs `pyodbc` + ODBC Driver 17/18 | Low — uses Agent-bundled connector |
| **Maintenance effort** | Medium — 1 Python file + 1 YAML | Low — 1 YAML |

---

## What's in this folder

```
mssql-db-audit-logs/
├── README.md                                  ← This file. Overview + comparison.
├── prerequisites.md                           ← Common prereqs for BOTH approaches.
│
├── datadog-custom-check/                      ← Approach A: Python custom check
│   ├── guideline.md
│   └── test_configs/
│       ├── conf.yaml
│       └── custom_ax_audit.py
│
└── datadog-mssql-custom-queries/              ← Approach B: sqlserver `custom_queries`
    ├── guideline.md
    └── test_configs/
        └── conf.yaml
```

**Start with [`prerequisites.md`](./prerequisites.md)** — six items that apply to
either approach. Then pick one approach and follow its `guideline.md`.

---

## Getting started

1. Read [`prerequisites.md`](./prerequisites.md) and complete the six common
   prerequisites on the target Windows VM.
2. Pick an approach.
3. Follow that approach's `guideline.md`:
   - [datadog-custom-check/guideline.md](./datadog-custom-check/guideline.md)
   - [datadog-mssql-custom-queries/guideline.md](./datadog-mssql-custom-queries/guideline.md)
4. Verify in Datadog Logs Explorer with `service:ax-2012`.

---

## The two approaches at a glance

### A. `datadog-custom-check/` — Python custom check (log collection)

Uses the Agent check [`send_log`](https://docs.datadoghq.com/logs/log_collection/agent_checks/)
API — the same pattern described in
[Create an Agent-based Integration](https://docs.datadoghq.com/extend/integrations/agent_integration/?tab=ootbintegration).
For packaged community checks, see
[Use Community and Marketplace Integrations](https://docs.datadoghq.com/agent/guide/use-community-integrations/?tab=hostinstallation)
and [integrations-extras](https://github.com/DataDog/integrations-extras).

A small Python file (`custom_ax_audit.py`) loaded by the Datadog Agent. Queries
`SYSDATABASELOG` and ships each row via `self.send_log()`. The last processed `RecId`
is stored in the Agent's `persistent_cache` (a key/value file on disk) so it survives
Agent restarts.

```
Agent scheduler ── every 60s ──▶ custom_ax_audit.py
                                        │
                                        ▼
                                 read RecId from persistent_cache
                                        │
                                        ▼
                                 SELECT WHERE RecId > <last>
                                        │
                                        ▼
                                 send_log() per row
                                        │
                                        ▼
                                 write_persistent_cache(new RecId)
```

### B. `datadog-mssql-custom-queries/` — sqlserver integration `custom_queries`

Uses the official [`sqlserver` integration](https://github.com/DataDog/integrations-core/tree/master/sqlserver)
from [integrations-core](https://github.com/DataDog/integrations-core) and its
[`custom_queries` feature](https://github.com/DataDog/integrations-core/blob/master/sqlserver/datadog_checks/sqlserver/data/conf.yaml.example)
with `extras: type: log`.

The official Datadog `sqlserver` integration's `custom_queries` feature with
`extras: type: log`. One YAML file. Uses a **sliding 60-second time window** in the
SQL `WHERE` clause to grab recent rows. No persistent cursor.

```
Agent scheduler ── every 60s ──▶ sqlserver integration
                                        │
                                        ▼
                                 SELECT WHERE CreatedDateTime >= now - 60s
                                        │
                                        ▼
                                 emit each row as a log
                                        │
                                        ▼
                                 (no cursor stored — re-poll the window next time)
```

---

## Side-by-side comparison

| Dimension | A. Custom check | B. `custom_queries` |
|---|---|---|
| **Files to maintain** | 2 (`custom_ax_audit.py` + `conf.yaml`) | 1 (`conf.yaml`) |
| **Language** | Python + SQL | YAML + SQL |
| **Watermark mechanism** | Persistent `RecId` cursor via `persistent_cache` | Sliding 60-second time window (no cursor) |
| **Drop risk on Agent restart** | **None** — cursor replays | **Yes** — rows in the gap silently lost |
| **Drop risk on slow check** | **None** | **Yes** — if check delays > 60s |
| **Backfill after extended downtime** | Yes — re-queries everything since last cursor | No — only re-queries the last 60s |
| **Install footprint** | Needs `pyodbc` + ODBC Driver 17/18 (Microsoft download) | None — uses Agent-bundled adodbapi (Windows) / FreeTDS (Linux) |
| **Adding a TableId** | Edit `table_ids:` in conf.yaml (1 place) | Edit `[Table] IN (...)` in the SQL (1 place) |
| **Adding a column** | Edit Python in 2 places (SELECT + send_log) | Edit YAML in 3 places (SQL + columns + extras.attributes) |
| **Cursor visibility** | Plain text file at `run\custom_ax_audit\last_recid` | None |
| **Cursor reset** | Edit one text file, restart Agent | Not applicable |
| **Datadog UX** | `service:ax-2012 source:ax-audit` | `service:ax-2012 source:sqlserver` |
| **Coexists with `sqlserver` integration metrics** | Yes — separate check | Yes — same conf.yaml carries both metrics and audit log emission |
| **First-time setup steps** | ~7 | ~3 |
| **Datadog support scope** | Customer owns check logic and inputs; Datadog delivers and displays collected data | Datadog supports the `sqlserver` integration; customer owns custom SQL and mappings |
| **Performance** | Single SQL query per minute (cursor-bounded result) | Single SQL query per minute (time-bounded result) |
| **CPU/memory on the VM** | Negligible | Negligible |


---

## What both have in common

- **Same source data.** `dbo.SYSDATABASELOG` filtered to in-scope TableIds with
  a 30-second `CreatedDateTime` settle window (in approach A) or 60-second time
  window (in approach B).
- **Same Datadog facets.** `@RecId`, `@LogType`, `@TableId`, `@LogRecId`,
  `@UserName`. The message body is the AX `Description` column.
- **Same prerequisites.** Datadog Agent, `logs_enabled: true`, SQL Server in Mixed
  Mode, `datadog` SQL login with SELECT, confirmed AX database name, Administrator
  access. All documented in [`prerequisites.md`](./prerequisites.md).
- **Coexists with the `sqlserver` integration.** Both approaches leave SQL Server
  health metrics flowing through the standard integration unaffected.

---

## Support & responsibility

See [Support & responsibility](../README.md#support--responsibility) in the root guide.

| Approach | Customer owns | Datadog owns |
|---|---|---|
| **A. Custom check** | Python script, SQL, cursor, source access | Data delivery & display |
| **B. `custom_queries`** | Custom SQL and field mappings | `sqlserver` integration |

---

## Datadog documentation

| Topic | Link |
|---|---|
| Datadog documentation | [docs.datadoghq.com](https://docs.datadoghq.com/) |
| Core Agent integrations | [DataDog/integrations-core](https://github.com/DataDog/integrations-core) |
| Community & Marketplace integrations | [Use Community and Marketplace Integrations](https://docs.datadoghq.com/agent/guide/use-community-integrations/?tab=hostinstallation) · [DataDog/integrations-extras](https://github.com/DataDog/integrations-extras) |
| Agent-based integration development | [Create an Agent-based Integration](https://docs.datadoghq.com/extend/integrations/agent_integration/?tab=ootbintegration) |
| Custom log collection from Agent checks | [Agent Integration Log Collection](https://docs.datadoghq.com/logs/log_collection/agent_checks/) |
| API-based integration development | [Create an API-based Integration](https://docs.datadoghq.com/extend/integrations/api_integration/) |

> **Note:** This use case is Agent-based (Approach A) or core-integration config (Approach B).
> An [API-based integration](https://docs.datadoghq.com/extend/integrations/api_integration/)
> would instead push logs from a separate service via the Datadog HTTP API — useful if
> collection cannot run on the Agent host.
