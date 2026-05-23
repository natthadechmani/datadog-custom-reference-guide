# Datadog Custom Reference Guide

Reference implementations for Datadog use cases that are **not supported out of the box**.
Each folder documents a real-world pattern — prerequisites, configuration, trade-offs, and
test configs — so teams can adapt proven approaches instead of starting from scratch.

These guides sit alongside official Datadog documentation and integrations. They are
**not official Datadog documentation, products, or support offerings.** This repository
is maintained by the team sharing these patterns for reference — not by Datadog.

## Support & responsibility

| | Customer owns | Datadog owns |
|---|---|---|
| **Custom Agent check** | Check logic, config, and data source availability | Delivering and displaying data the check collects |
| **Integration config** (e.g. `sqlserver` `custom_queries`) | Custom SQL and field mappings | The integration itself |

Use these as starting points — test in your environment before production.

## Official Datadog references

| Topic | Link |
|---|---|
| Datadog documentation | [docs.datadoghq.com](https://docs.datadoghq.com/) |
| Core Agent integrations | [DataDog/integrations-core](https://github.com/DataDog/integrations-core) |
| Community & Marketplace integrations | [Use Community and Marketplace Integrations](https://docs.datadoghq.com/agent/guide/use-community-integrations/?tab=hostinstallation) · [DataDog/integrations-extras](https://github.com/DataDog/integrations-extras) |
| Agent-based integration development | [Create an Agent-based Integration](https://docs.datadoghq.com/extend/integrations/agent_integration/?tab=ootbintegration) |
| Custom log collection from Agent checks | [Agent Integration Log Collection](https://docs.datadoghq.com/logs/log_collection/agent_checks/) |
| API-based integration development | [Create an API-based Integration](https://docs.datadoghq.com/extend/integrations/api_integration/) |

## Use cases

| Use case | Description |
|---|---|
| [mssql-db-audit-logs](./mssql-db-audit-logs/) | Ingest Microsoft Dynamics AX 2012 R3 SQL Server audit rows (`dbo.SYSDATABASELOG`) into Datadog Logs via a Python custom check or the `sqlserver` integration's `custom_queries` feature |

## When to use which integration pattern

**Agent-based integration** — Run logic on the Datadog Agent (Python custom check or
community integration). Best when the Agent has network access to the source system and
you need periodic polling, metrics, service checks, or log emission via `send_log`.
See [Create an Agent-based Integration](https://docs.datadoghq.com/extend/integrations/agent_integration/?tab=ootbintegration)
and [Agent Integration Log Collection](https://docs.datadoghq.com/logs/log_collection/agent_checks/).

**API-based integration** — Push data to Datadog over HTTPS from your own service.
Best for SaaS products or centralized collectors that call the
[Datadog API](https://docs.datadoghq.com/api/latest/using-the-api/).
See [Create an API-based Integration](https://docs.datadoghq.com/extend/integrations/api_integration/).

**Community / extras integration** — Install a packaged integration from
[integrations-extras](https://github.com/DataDog/integrations-extras) when one already
exists for your source. See
[Use Community and Marketplace Integrations](https://docs.datadoghq.com/agent/guide/use-community-integrations/?tab=hostinstallation).

**Core integration feature** — Extend an existing integration in
[integrations-core](https://github.com/DataDog/integrations-core) (for example,
`sqlserver` `custom_queries`) when the official integration already covers your source
but needs a config-only customization.
