create schema if not exists iot;
create schema if not exists analytics;

create table if not exists public.device_config (
  device_id varchar(50) primary key,
  calibration double precision not null,
  turns double precision not null,
  alpha double precision not null check (alpha >= 0 and alpha <= 1)
);

create table if not exists iot.raw_sensor_readings (
  id bigint generated always as identity primary key,
  machine_id varchar(50) not null,
  event_ts timestamptz not null default now(),
  temp_c double precision not null,
  current_a double precision not null,
  vibration_rms_g double precision not null,
  load_level varchar(20) not null,
  source varchar(20) not null default 'esp32',
  processed boolean not null default false,
  processed_at timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists analytics.sensor_predictions (
  id bigint generated always as identity primary key,
  raw_reading_id bigint not null unique references iot.raw_sensor_readings(id) on delete cascade,
  machine_id varchar(50) not null,
  event_ts timestamptz not null,
  temp_c double precision not null,
  current_a double precision not null,
  vibration_rms_g double precision not null,
  load_level varchar(20) not null,
  predicted_status varchar(20) not null,
  confidence double precision not null,
  alert_flag boolean not null default false,
  maintenance_action varchar(100) not null,
  model_version varchar(50) not null default 'rf_v1',
  created_at timestamptz not null default now()
);

create table if not exists analytics.generated_reports (
  id bigint generated always as identity primary key,

  machine_id varchar(50) not null,

  report_type varchar(30) not null default 'performance',

  period_start date not null,
  period_end date not null,

  generated_at timestamptz not null default now(),

  total_readings bigint not null default 0,

  normal_count bigint not null default 0,
  warning_count bigint not null default 0,
  failure_count bigint not null default 0,

  normal_percentage double precision,
  warning_percentage double precision,
  failure_percentage double precision,

  temperature_avg double precision,
  temperature_min double precision,
  temperature_max double precision,

  current_avg double precision,
  current_min double precision,
  current_max double precision,

  vibration_avg double precision,
  vibration_min double precision,
  vibration_max double precision,

  overall_status varchar(20),

  summary text,

  report_data jsonb not null default '{}'::jsonb,

  file_path text,

  created_at timestamptz not null default now(),

);

create index if not exists idx_generated_reports_machine
  on analytics.generated_reports(machine_id);

create index if not exists idx_generated_reports_period
  on analytics.generated_reports(period_start, period_end);

create index if not exists idx_raw_readings_event_ts on iot.raw_sensor_readings(event_ts desc);
create index if not exists idx_raw_readings_processed on iot.raw_sensor_readings(processed, id);
create index if not exists idx_predictions_event_ts on analytics.sensor_predictions(event_ts desc);
create index if not exists idx_predictions_status on analytics.sensor_predictions(predicted_status);

grant usage on schema iot to anon, authenticated, service_role;
grant usage on schema analytics to anon, authenticated, service_role;

grant select, insert, update on public.device_config to anon, authenticated, service_role;
grant select, insert, update on all tables in schema iot to anon, authenticated, service_role;
grant select on all tables in schema analytics to anon, authenticated, service_role;
grant insert on all tables in schema analytics to service_role;

grant usage, select on all sequences in schema iot to anon, authenticated, service_role;
grant usage, select on all sequences in schema analytics to anon, authenticated, service_role;

alter default privileges in schema iot
grant select, insert, update on tables to anon, authenticated, service_role;

alter default privileges in schema analytics
grant select on tables to anon, authenticated, service_role;

alter default privileges in schema analytics
grant insert on tables to service_role;

alter default privileges in schema iot
grant usage, select on sequences to anon, authenticated, service_role;

alter default privileges in schema analytics
grant usage, select on sequences to anon, authenticated, service_role;


-- ============================================================
-- GENERATED PERFORMANCE REPORTS
-- ============================================================

grant select, insert, update
on analytics.generated_reports
to anon;

grant usage, select
on sequence analytics.generated_reports_id_seq
to anon;

alter table analytics.generated_reports enable row level security;

create policy "Allow anon to create performance reports"
on analytics.generated_reports
for insert
to anon
with check (
    report_type = 'performance'
);

create policy "Allow anon to read performance reports"
on analytics.generated_reports
for select
to anon
using (
    report_type = 'performance'
);

create policy "Allow anon to update performance reports"
on analytics.generated_reports
for update
to anon
using (
    report_type = 'performance'
)
with check (
    report_type = 'performance'
);

-- ============================================================
-- STORAGE: MAINTENANCE REPORTS
-- ============================================================

create policy "Allow anon to upload maintenance reports"
on storage.objects
for insert
to anon
with check (
    bucket_id = 'maintenance-reports'
);

create policy "Allow anon to read maintenance reports"
on storage.objects
for select
to anon
using (
    bucket_id = 'maintenance-reports'
);

create policy "Allow anon to update maintenance reports"
on storage.objects
for update
to anon
using (
    bucket_id = 'maintenance-reports'
)
with check (
    bucket_id = 'maintenance-reports'
);

