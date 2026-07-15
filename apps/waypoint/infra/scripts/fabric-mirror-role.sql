-- Idempotent PostgreSQL role bootstrap for Fabric Mirroring of the Waypoint financial core.
--
-- NOTE: In the deployed (keystone/CI) path this SQL is NOT used — the API bootstraps the
-- mirroring role at startup (api/app/common/database.py::_bootstrap_fabric_mirroring_role),
-- which keeps the deploy pipeline free of any admin connection or psql. This file is retained
-- for LOCAL/OUT-OF-BAND validation only, invoked by provision-fabric-mirror.sh when
-- WAYPOINT_FABRIC_MIRROR_ADMIN_CONNECTION is explicitly set. Keep it in lockstep with the
-- Python bootstrap.
--
-- Fabric mirroring connects to the source server as a dedicated role that must have
-- CREATEDB, CREATEROLE, LOGIN, REPLICATION and the azure_cdc_admin role, AND must OWN the
-- tables it mirrors (inherited from PostgreSQL CREATE PUBLICATION ownership rules). This
-- script provisions that role, transfers ownership of the four mirrored tables to it, and
-- re-grants the application role the DML it needs so the API keeps writing after the
-- ownership change. It is safe to re-run: every step is guarded or naturally idempotent.
--
-- Invoke with psql variables (never hard-code the password):
--   psql "<admin connection>" \
--     -v fabric_user=fabric_user \
--     -v fabric_password="$FABRIC_MIRROR_PASSWORD" \
--     -v app_user=waypoint_app \
--     -v mirror_db=waypoint \
--     -f fabric-mirror-role.sql
--
-- Notes:
--   * psql does not interpolate :'var' inside dollar-quoted blocks, so the parameters are
--     stashed as session settings and read back with current_setting() inside the do blocks.
--   * azure_cdc_admin only exists on Azure Database for PostgreSQL flexible server; the grant
--     is guarded so this same script also applies cleanly against a vanilla PostgreSQL used
--     for local validation.
--   * The mirrored table set is the financial/transactional reconciliation core only.

\set ON_ERROR_STOP on

select set_config('waypoint.fabric_user', :'fabric_user', false);
select set_config('waypoint.fabric_password', :'fabric_password', false);
select set_config('waypoint.app_user', :'app_user', false);

-- 1) Create (or update) the Fabric mirroring login role with the required attributes.
do $$
declare
    role_name text := current_setting('waypoint.fabric_user');
    role_pw text := current_setting('waypoint.fabric_password');
begin
    if not exists (select 1 from pg_roles where rolname = role_name) then
        execute format(
            'create role %I with login createdb createrole replication password %L',
            role_name, role_pw
        );
    else
        execute format(
            'alter role %I with login createdb createrole replication password %L',
            role_name, role_pw
        );
    end if;
end $$;

-- 2) Grant the Azure CDC management role when present (Azure Database for PostgreSQL only).
do $$
declare
    role_name text := current_setting('waypoint.fabric_user');
begin
    if exists (select 1 from pg_roles where rolname = 'azure_cdc_admin') then
        execute format('grant azure_cdc_admin to %I', role_name);
    else
        raise notice 'azure_cdc_admin role not present (non-Azure PostgreSQL) - skipping grant.';
    end if;
end $$;

-- 3) Allow the mirroring role to CREATE (publications/objects) in the mirrored database and
--    use the public schema that owns the mirrored tables.
grant create on database :"mirror_db" to :"fabric_user";
grant usage, create on schema public to :"fabric_user";

-- 4) Transfer ownership of the four mirrored tables to the mirroring role (required for
--    CREATE PUBLICATION), then re-grant the application role full DML so writes continue.
do $$
declare
    role_name text := current_setting('waypoint.fabric_user');
    app_name text := current_setting('waypoint.app_user');
    mirrored_table text;
begin
    foreach mirrored_table in array array[
        'suppliers', 'invoices', 'invoice_lines', 'reconciliation_findings'
    ]
    loop
        if exists (
            select 1 from information_schema.tables
            where table_schema = 'public' and table_name = mirrored_table
        ) then
            execute format('alter table public.%I owner to %I', mirrored_table, role_name);
            execute format(
                'grant select, insert, update, delete on public.%I to %I',
                mirrored_table, app_name
            );
        else
            raise notice 'mirrored table public.% does not exist yet - skipping.', mirrored_table;
        end if;
    end loop;
end $$;
