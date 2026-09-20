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

-- RLS (row-level security) is enabled on all four tables with no policies:
-- only the service-role key (collectors) can read or write them directly.
alter table public.traffic_frames enable row level security;
alter table public.traffic_detections enable row level security;
alter table public.traffic_video_counts enable row level security;
alter table public.camera_refs enable row level security;

-- Early helper view (hourly averages, Central time); not used by the Vercel dashboard.
-- create view public.traffic_hourly with (security_invoker = on) as ...

-- Dashboard RPC functions: security definer, read-only aggregates, granted to anon.
--   traffic_dashboard(win text default '6h') returns json
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
