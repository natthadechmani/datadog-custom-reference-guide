# Prerequisites — common across both approaches

These prerequisites apply to BOTH approaches in this folder:

- [`datadog-custom-check/`](./datadog-custom-check/) — Python custom check.
- [`datadog-mssql-custom-queries/`](./datadog-mssql-custom-queries/) — SQL Server integration `custom_queries` with `extras: type: log`.

Each approach's own `guideline.md` has a short list of approach-specific prerequisites
on top of these.

---

## 1. Datadog Agent v7.21+

Check version:

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" version
```

If not installed or too old, install the latest Datadog Agent MSI for Windows from
<https://docs.datadoghq.com/agent/>.

---

## 2. `logs_enabled: true` in datadog.yaml

Check:

```powershell
Select-String -Path "$env:ProgramData\Datadog\datadog.yaml" -Pattern 'logs_enabled'
```

If missing or `false`, edit `C:\ProgramData\Datadog\datadog.yaml`:

```yaml
logs_enabled: true
```

Then restart the Agent:

```powershell
& "$env:ProgramFiles\Datadog\Datadog Agent\bin\agent.exe" restart-service
```

---

## 3. SQL Server in Mixed Mode authentication

By default, fresh SQL Server installs are Windows-auth-only — SQL logins won't work
until Mixed Mode is enabled.

https://learn.microsoft.com/en-us/sql/database-engine/configure-windows/change-server-authentication-mode?view=sql-server-ver17&tabs=ssms

Check (in SSMS, as a sysadmin):

```sql
SELECT SERVERPROPERTY('IsIntegratedSecurityOnly');
-- 0 = Mixed Mode (good), 1 = Windows-only (bad)
```

If it returns `1`, switch to Mixed Mode. Run as sysadmin:

```sql
EXEC xp_instance_regwrite
    N'HKEY_LOCAL_MACHINE',
    N'Software\Microsoft\MSSQLServer\MSSQLServer',
    N'LoginMode', REG_DWORD, 2;   -- 1 = Windows only, 2 = Mixed
```

Then restart the SQL Server service (required for the change to take effect):

```powershell
Restart-Service -Name 'MSSQLSERVER' -Force
```

(For a named instance the service is `MSSQL$<INSTANCE_NAME>`.)

Verify by logging into SSMS as `datadog` with SQL Server Authentication (the login
you create in section 4). If the login succeeds, Mixed Mode is on.

---

## 4. `datadog` SQL login with SELECT on `dbo.SYSDATABASELOG`

Check (in SSMS, as a sysadmin):

```sql
SELECT name, is_disabled FROM sys.sql_logins WHERE name = 'datadog';
```

If missing, create it. Replace `<ax_database>` with the real database name from
section 5:

```sql
USE master;
GO
CREATE LOGIN datadog WITH PASSWORD = '<strong-password>',
                          CHECK_POLICY = OFF,
                          CHECK_EXPIRATION = OFF;
GO
CREATE USER datadog FOR LOGIN datadog;
GRANT CONNECT TO datadog;
GO

USE <ax_database>;
GO
CREATE USER datadog FOR LOGIN datadog;
GRANT SELECT ON OBJECT::dbo.SYSDATABASELOG TO datadog;
GO
```

Verify the grant:

```sql
USE <ax_database>;
SELECT perm.permission_name, perm.state_desc,
       OBJECT_NAME(perm.major_id) AS object_name,
       USER_NAME(perm.grantee_principal_id) AS grantee
FROM sys.database_permissions perm
WHERE USER_NAME(perm.grantee_principal_id) = 'datadog';
```

You should see one row showing `SELECT | GRANT | SYSDATABASELOG | datadog`.

---

## 5. Confirm the AX database name

Find the database that contains `dbo.SYSDATABASELOG`:

```sql
SELECT name FROM sys.databases
WHERE name LIKE '%AX%' OR name LIKE '%Dynamics%';
```

Or in SSMS Object Explorer: expand **Databases**, drill into each candidate, and find
`dbo.SYSDATABASELOG` under **Tables**.

Common values: `MicrosoftDynamicsAX`, `DynamicsAX_PROD`, `FINAX67_GOLIVE_2024`.
Note the exact name — you'll use it in `conf.yaml`.

---

## 6. Administrator access on the VM

Required for installing drivers, registering Event sources, and modifying Agent
service config. Open PowerShell as Administrator:

- Right-click Start menu → **Windows PowerShell (Admin)** / **Terminal (Admin)**, OR
- Search for PowerShell, right-click, **Run as administrator**.

---

## Next

Once these six common prerequisites are in place, continue to the approach-specific
guide:

- [datadog-custom-check/guideline.md](./datadog-custom-check/guideline.md)
- [datadog-mssql-custom-queries/guideline.md](./datadog-mssql-custom-queries/guideline.md)

Each guide has a short "approach-specific prerequisites" section at the top covering
only what's unique to that approach (e.g. pyodbc for the custom check).
