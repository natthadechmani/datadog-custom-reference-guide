# MS SQL Server → Datadog Logs — AX 2012 R3 audit log pipelines

> Part of the [Datadog Custom Reference Guide](../README.md) — reference implementations
> for Datadog use cases not supported out of the box. **Not official Datadog documentation
> or support.** See [Support & responsibility](../README.md#support--responsibility).

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

## Pros and cons

### A. Custom check (`datadog-custom-check/`)

**Pros:**

- **Durable.** Persistent `RecId` cursor means restarts, GC pauses, and slow queries
  never cause silent drops.
- **Reliable backfill.** If the Agent is down for 6 hours, the next run reads every
  RecId that appeared during the gap.
- **Operationally observable.** The cursor file (`last_recid`) is readable. You can
  spot a stuck cursor in 5 seconds.
- **Cursor reset is a one-line text edit.**

**Cons:**

- **Python in production.** The custom check is the customer's responsibility — check
  logic, SQL, cursor handling, and source availability. Datadog is responsible for
  delivering the data the check submits and displaying it in the app. If there is a
  bug in the script, the customer owns the fix.
- **`pyodbc` install** into the Agent's embedded Python is unofficial and may be
  wiped by Agent upgrades.
- **Two files** to maintain instead of one. New columns require edits in two places
  (SELECT and send_log).
- **More moving parts to debug** when something goes wrong (Agent + check + cursor
  file).

**Best for:** customers where every audit row matters (legal / compliance), or
environments with frequent Agent restarts (Windows Update reboots), or teams comfortable
maintaining Python.

### B. `custom_queries` (`datadog-mssql-custom-queries/`)

**Pros:**

- **Config-only.** One YAML file. No Python to deploy or maintain.
- **No install steps.** Uses the Agent's bundled DB connector — no `pip install`, no
  ODBC driver download required on Windows.
- **Officially supported integration.** Datadog supports the `sqlserver` integration
  itself. The customer is responsible for the custom query, field mappings, and
  ensuring the query returns the expected audit data.
- **Survives Agent upgrades cleanly** — nothing in the Agent's embedded Python to
  break.
- **Shares the integration** with SQL Server metrics — one conf.yaml for both.

**Cons:**

- **Silent drops possible.** The sliding 60-second window has no persistent state.
  Any check delay > 60s loses data forever.
- **No replay after downtime.** Restart the Agent for 5 minutes? Lose 4+ minutes of
  audit rows.
- **Three places to edit** when adding a column (SQL + columns + extras.attributes).
- **No cursor file to inspect** — debugging drop scenarios is harder.

**Best for:** customers who can tolerate occasional missing audit rows (the source of
truth is still SQL — they can reconcile by RecId), or environments where Python
deployment is operationally infeasible, or teams that prefer "fewer moving parts" to
"zero drops".

---

## Decision tree

**Pick A (custom check) if any of:**

- The audit data is compliance-grade (every row must be captured).
- The Agent restarts frequently (Windows Update reboots common).
- The customer has prior bad experience with silent data drops.
- You're comfortable owning a small Python file.

**Pick B (`custom_queries`) if any of:**

- The audit data is "nice to have" for searching, not "every row required".
- The customer's ops team strongly prefers config-only deployments.
- You need a clear path to Datadog support without "but it's custom Python" caveats.
- You don't want to manage `pyodbc` across Agent upgrades.

**When in doubt:** start with B for simplicity. Reconcile against the SQL source
table for the first week to confirm the drop rate is acceptable. If you see drops you
can't tolerate, switch to A — both produce the same logs in Datadog, so the
downstream queries / monitors / dashboards keep working.

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

## Getting started

1. Read [`prerequisites.md`](./prerequisites.md) and complete the six common
   prerequisites on the target Windows VM.
2. Pick an approach.
3. Follow that approach's `guideline.md`:
   - [datadog-custom-check/guideline.md](./datadog-custom-check/guideline.md)
   - [datadog-mssql-custom-queries/guideline.md](./datadog-mssql-custom-queries/guideline.md)
4. Verify in Datadog Logs Explorer with `service:ax-2012`.

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
