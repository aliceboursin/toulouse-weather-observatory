-- Missing intervals: every run of consecutive 15-minute slots with no valid
-- reading, per station, between the start of the loaded window and the time
-- the observations were collected.
--
-- A gap that reaches the collection time is marked ongoing: the station had
-- not reported again when the snapshot was taken.

with raw as (

    select * from {{ source('raw', 'observations') }}

),

snapshot as (

    select
        max(ingested_at) as collected_at,
        timestamp(date(min(observed_at))) as window_start,
        -- Last 15-minute slot at or before the collection time.
        timestamp_seconds(div(unix_seconds(max(ingested_at)), 900) * 900) as last_slot
    from raw

),

readings as (

    select
        station_id,
        observed_at,
        lag(observed_at) over (partition by station_id order by observed_at) as previous_at
    from {{ ref('stg_observations') }}

),

between_readings as (

    select
        station_id,
        timestamp_add(previous_at, interval 15 minute) as gap_start,
        timestamp_sub(observed_at, interval 15 minute) as gap_end,
        false as is_ongoing
    from readings
    where timestamp_diff(observed_at, previous_at, minute) > 15

),

bounds as (

    select
        stations.station_id,
        min(observations.observed_at) as first_at,
        max(observations.observed_at) as last_at
    from {{ ref('stg_stations') }} as stations
    left join {{ ref('stg_observations') }} as observations
        on observations.station_id = stations.station_id
    group by stations.station_id

),

before_first_reading as (

    select
        bounds.station_id,
        snapshot.window_start as gap_start,
        timestamp_sub(bounds.first_at, interval 15 minute) as gap_end,
        false as is_ongoing
    from bounds
    cross join snapshot
    where bounds.first_at > snapshot.window_start

),

after_last_reading as (

    -- Also covers a station with no valid reading at all.
    select
        bounds.station_id,
        coalesce(timestamp_add(bounds.last_at, interval 15 minute), snapshot.window_start)
            as gap_start,
        snapshot.last_slot as gap_end,
        true as is_ongoing
    from bounds
    cross join snapshot
    where bounds.last_at is null or bounds.last_at < snapshot.last_slot

),

gaps as (

    select * from between_readings
    union all
    select * from before_first_reading
    union all
    select * from after_last_reading

)

select
    gaps.station_id,
    stations.station_label,
    gaps.gap_start,
    gaps.gap_end,
    div(timestamp_diff(gaps.gap_end, gaps.gap_start, minute), 15) + 1 as missing_readings,
    round((timestamp_diff(gaps.gap_end, gaps.gap_start, minute) + 15) / 60, 2) as gap_hours,
    gaps.is_ongoing
from gaps
left join {{ ref('stg_stations') }} as stations
    on stations.station_id = gaps.station_id
