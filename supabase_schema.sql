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
