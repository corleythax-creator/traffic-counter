-- Traffic counter: database objects in Supabase project fuel-model (ref wcrsomethkjlfhhuurmh).
-- Reference snapshot, not a migration. Dump live definitions with:
--   select pg_get_functiondef('public.traffic_dashboard(text)'::regprocedure);
--   select pg_get_functiondef('public.traffic_camera(text,text)'::regprocedure);
-- The same database also holds the separate home-cameras project's homecam_* tables and
-- homecam storage bucket (documented in that project) and unrelated land-records tables.

create table public.traffic_frames (
  camera          text not null,
  epoch_ms        bigint not null,          -- capture time in ms; with camera, the key
  captured_at     timestamptz not null,
  file            text,                     -- original image name (image is deleted after upload)
  bytes           int,
  md5             text,
  car             int,
  truck           int,
  bus             int,
  motorcycle      int,
  total           int,                      -- vehicles counted in the zone
  people          int,                      -- people anywhere in the frame (conf >= 0.25)
  model           text,                     -- yolo11m.pt (current) or yolo11n.pt (pre Sep 18 2026 home script)
  brightness      real,                     -- mean pixel value 0-255
  conf_threshold  real,
  view_status     text,                     -- same | shifted | changed | night
  source          text,                     -- local | github
  primary key (camera, epoch_ms)
);
create index traffic_frames_captured_at_idx on public.traffic_frames (captured_at);
create index traffic_frames_camera_time_idx on public.traffic_frames (camera, captured_at);
create index traffic_frames_camera_source_time_idx on public.traffic_frames (camera, source, captured_at desc);
-- Covering indexes for the dashboard functions, added Oct 12 2026: they let the 7-day
-- windows read the index alone instead of the wide table, which does not stay in memory
-- (7-day reads went from 5 s to 0.4 s, under the 3 s anon statement timeout).
create index traffic_frames_time_cover_idx on public.traffic_frames (captured_at)
  include (camera, total, people, view_status);                          -- traffic_dashboard
create index traffic_frames_camera_time_cover_idx on public.traffic_frames (camera, captured_at)
  include (total, people, car, truck, bus, motorcycle);                  -- traffic_camera

-- Hour-of-day rollup of traffic_frames, kept exactly in step by the trigger below. The
-- dashboard functions read hour-of-day averages and the camera list from here: computing
-- them from every frame took 12 s at 658k frames (Oct 12 2026) and broke the dashboard.
create table public.traffic_hour_stats (
  camera    text     not null,
  day       date     not null,              -- Central (America/Chicago) date
  hr        smallint not null,              -- Central hour 0-23
  n         integer  not null default 0,    -- frames
  n_total   integer  not null default 0,    -- frames with a non-null total
  sum_total bigint   not null default 0,    -- avg = sum_total / n_total, same as avg(total)
  night     integer  not null default 0,    -- frames with view_status = 'night'
  primary key (camera, day, hr)
);

create or replace function public.traffic_hour_stats_apply()
returns trigger language plpgsql security definer set search_path = public as $fn$
begin
  if tg_op = 'UPDATE' and old.camera = new.camera and old.captured_at = new.captured_at
     and old.total is not distinct from new.total
     and old.view_status is not distinct from new.view_status then
    return null;
  end if;
  if tg_op in ('UPDATE', 'DELETE') then
    update traffic_hour_stats set n = n - 1, n_total = n_total - (old.total is not null)::int,
      sum_total = sum_total - coalesce(old.total, 0),
      night = night - (coalesce(old.view_status, '') = 'night')::int
    where camera = old.camera and day = (old.captured_at at time zone 'America/Chicago')::date
      and hr = extract(hour from old.captured_at at time zone 'America/Chicago');
  end if;
  if tg_op in ('INSERT', 'UPDATE') then
    insert into traffic_hour_stats as s (camera, day, hr, n, n_total, sum_total, night)
    values (new.camera, (new.captured_at at time zone 'America/Chicago')::date,
            extract(hour from new.captured_at at time zone 'America/Chicago'),
            1, (new.total is not null)::int, coalesce(new.total, 0),
            (coalesce(new.view_status, '') = 'night')::int)
    on conflict (camera, day, hr) do update set n = s.n + 1, n_total = s.n_total + excluded.n_total,
      sum_total = s.sum_total + excluded.sum_total, night = s.night + excluded.night;
  end if;
  return null;
end $fn$;
revoke all on function public.traffic_hour_stats_apply() from public, anon, authenticated;
create trigger traffic_hour_stats_trg after insert or update or delete on public.traffic_frames
for each row execute function public.traffic_hour_stats_apply();

-- To rebuild it (e.g. after a truncate, which the trigger does not see), recompute a
-- range of Central dates at a time under a short write lock, overwriting by upsert:
--   lock table public.traffic_frames in share row exclusive mode;
--   insert into public.traffic_hour_stats as s (camera, day, hr, n, n_total, sum_total, night)
--   select camera, (captured_at at time zone 'America/Chicago')::date,
--          extract(hour from captured_at at time zone 'America/Chicago'),
--          count(*), count(total), coalesce(sum(total), 0), count(*) filter (where view_status = 'night')
--   from public.traffic_frames
--   where captured_at >= ('2026-10-01'::timestamp at time zone 'America/Chicago')
--     and captured_at <  ('2026-10-08'::timestamp at time zone 'America/Chicago')
--   group by 1, 2, 3
--   on conflict (camera, day, hr) do update set n = excluded.n, n_total = excluded.n_total,
--     sum_total = excluded.sum_total, night = excluded.night;
-- A whole-table pass in one go takes over a minute on this database's disk and holds
-- the lock that long; a week at a time took a few seconds each.

create table public.traffic_detections (
  camera     text not null,
  epoch_ms   bigint not null,
  idx        smallint not null,
  class      text not null,                 -- car | truck | bus | motorcycle | person
  conf       real not null,
  x1 real not null, y1 real not null, x2 real not null, y2 real not null,  -- 0-1 fractions
  in_zone    boolean,
  full_pass  boolean,                       -- found by the whole-image pass (vs zoomed tiles only)
  primary key (camera, epoch_ms, idx),
  foreign key (camera, epoch_ms) references public.traffic_frames (camera, epoch_ms) on delete cascade
);
create index traffic_detections_epoch_ms_idx on public.traffic_detections (epoch_ms);

create table public.traffic_video_counts (
  camera      text not null,
  started_at  timestamptz not null,
  seconds     real not null,                -- video analysed; 0 when skipped
  line        text not null,
  direction   text not null,                -- toward | away (relative to the camera)
  vehicles    int,                          -- null when the camera view had changed
  view_status text,
  source      text,
  primary key (camera, started_at, line, direction)
);

create table public.camera_refs (
  camera     text primary key,              -- slug, or video:<slug> for video cameras
  ref_jpg    text not null,                 -- base64 320x240 grayscale JPEG, the median of several frames
                                            -- (a single frame matches too weakly to judge a camera move)
  zone       jsonb not null,                -- [[x, y], ...] fractions
  created_at timestamptz not null default now()  -- set on every write; drives the periodic re-base
);

-- RLS (row-level security) is enabled on all five tables with no policies:
-- only the service-role key (collectors) can read or write them directly.
alter table public.traffic_hour_stats enable row level security;
alter table public.traffic_frames enable row level security;
alter table public.traffic_detections enable row level security;
alter table public.traffic_video_counts enable row level security;
alter table public.camera_refs enable row level security;

-- Early helper view (hourly averages, Central time); not used by the Vercel dashboard.
-- create view public.traffic_hourly with (security_invoker = on) as ...

-- Dashboard RPC functions: security definer, read-only aggregates, granted to anon.
-- The anon role has statement_timeout = 3s; both functions must finish well inside it
-- (Oct 12 2026: 1h-24h windows 0.1-0.6 s, 7d 0.85 s; traffic_camera 7d 0.25 s).
-- Both run with `set work_mem = '32MB'` so their sorts stay in memory, and take
-- hourly[] and the camera list from traffic_hour_stats rather than traffic_frames.
-- traffic_dashboard reads the window once, grouped by camera, and finds peak{} from the
-- series buckets (latest bucket with the highest peak, then that frame), not by sorting
-- every frame in the window.
--   traffic_dashboard(win text default '2h') returns json
--     win: 1h (1-min buckets) | 2h (1-min, the front page default) | 8h (5-min) | 24h (30-min)
--          | 7d (2-hour); 6h (15-min) still answers but the page no longer asks for it
--     { now, cams: { <slug>: { latest{at,total,people,model,view}, avg_5, avg_15, avg_60,
--                              frames, avg, people_avg, peak{at,total},
--                              last15[{t,avg,peak,n}]  -- 1-min buckets, always the last 15 min
--                              night_frames, fallback_frames, day_avg,  -- comparability
--                              series[{t,avg,peak,n,night,fallback}], hourly[{hr,avg,n,night}] } },
--       video: [{camera, t, line, veh, secs}] }
--   traffic_camera(cam text, win text default '1h') returns json
--     win: 1h (1-min buckets) | 3h (1-min) | 6h (5-min) | 12h (1-min)
--          | 24h (15-min) | 7d (1-hour)   -- 3h and 12h keep minute detail on purpose
--     { now, camera, latest{at,total,people,view,source}, frames, avg, people_avg, peak,
--       mix{car,truck,bus,motorcycle}, series[{t,avg,peak,n,people}], hourly[{hr,avg,n}] }
--     'avg' is the window average the detail page draws as its period-average line.
-- grant execute on function public.traffic_dashboard(text) to anon, authenticated;
-- grant execute on function public.traffic_camera(text, text) to anon, authenticated;

-- Manual cleanup helper (not scheduled; detections are kept indefinitely by choice):
--   public.purge_old_traffic_detections(keep_days int default 30) returns bigint
