-- Reporting history: valid and flagged readings per station and UTC day,
-- against the number of 15-minute slots expected that day.
--
-- The data is a snapshot. The last day only expects slots up to the time the
-- observations were collected.

with raw as (

    select * from {{ source('raw', 'observations') }}

),

snapshot as (

    select
        max(ingested_at) as collected_at,
        date(min(observed_at)) as first_day
    from raw

),

days as (

    select
        day,
        collected_at,
        -- Slots from 00:00 up to the collection time, at most 96 a day.
        least(96, div(timestamp_diff(collected_at, timestamp(day), minute), 15) + 1)
            as expected_readings
    from snapshot,
        unnest(generate_date_array(first_day, date(collected_at))) as day

),

valid as (

    select
        station_id,
        date(observed_at) as day,
        count(*) as valid_readings
    from {{ ref('stg_observations') }}
    group by station_id, day

),

flagged as (

    -- Readings of exactly -50 °C, the value flagged during profiling.
    select
        station_id,
        date(observed_at) as day,
        count(*) as flagged_readings
    from raw
    where temperature_c = -50
    group by station_id, day

)

select
    stations.station_id,
    stations.station_label,
    days.day,
    days.expected_readings,
    coalesce(valid.valid_readings, 0) as valid_readings,
    coalesce(flagged.flagged_readings, 0) as flagged_readings,
    days.expected_readings - coalesce(valid.valid_readings, 0) as missing_readings,
    round(safe_divide(coalesce(valid.valid_readings, 0), days.expected_readings), 4)
        as daily_coverage_rate
from {{ ref('stg_stations') }} as stations
cross join days
left join valid
    on valid.station_id = stations.station_id
    and valid.day = days.day
left join flagged
    on flagged.station_id = stations.station_id
    and flagged.day = days.day
