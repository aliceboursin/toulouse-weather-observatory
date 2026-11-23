-- Station monitoring: one row per station with its latest valid reading,
-- recent coverage, flagged readings and a status.
--
-- Everything is measured against collected_at, the time the observations
-- were fetched. The data is a snapshot, not a live feed.
--
-- Status thresholds (first match wins):
--   1. no valid reading in the 24 hours before collection
--   2. latest valid reading more than 2 hours before collection (stale)
--   3. at least one flagged (-50 °C) reading in the 24 hours before collection
--   4. fewer than 80% of the 96 expected readings in those 24 hours
--   otherwise OK

{% set stale_after_hours = 2 %}
{% set low_coverage_below = 0.8 %}

with raw as (

    select * from {{ source('raw', 'observations') }}

),

snapshot as (

    -- Collection time of the load that holds the most recent observation.
    -- A backfill of older dates is ingested later, so max(ingested_at)
    -- would wrongly make the newest data look freshly collected.
    select array_agg(ingested_at order by observed_at desc limit 1)[offset(0)] as collected_at
    from raw

),

valid as (

    select
        observations.station_id,
        max(observations.observed_at) as latest_valid_reading_at,
        array_agg(observations.temperature_c order by observations.observed_at desc limit 1)[offset(0)]
            as latest_valid_temperature_c,
        countif(observations.observed_at > timestamp_sub(snapshot.collected_at, interval 24 hour))
            as valid_readings_24h,
        countif(observations.observed_at > timestamp_sub(snapshot.collected_at, interval 7 day))
            as valid_readings_7d
    from {{ ref('stg_observations') }} as observations
    cross join snapshot
    group by observations.station_id

),

flagged as (

    select
        raw.station_id,
        countif(raw.observed_at > timestamp_sub(snapshot.collected_at, interval 24 hour))
            as flagged_readings_24h,
        countif(raw.observed_at > timestamp_sub(snapshot.collected_at, interval 7 day))
            as flagged_readings_7d,
        count(*) as flagged_readings_total
    from raw
    cross join snapshot
    where raw.temperature_c = -50
    group by raw.station_id

),

longest_gap as (

    select
        station_id,
        max(gap_hours) as longest_gap_hours
    from {{ ref('station_observation_gaps') }}
    group by station_id

),

measured as (

    select
        stations.station_id,
        stations.station_label,
        snapshot.collected_at,
        valid.latest_valid_reading_at,
        valid.latest_valid_temperature_c,
        round(timestamp_diff(snapshot.collected_at, valid.latest_valid_reading_at, minute) / 60, 2)
            as hours_since_latest_valid_reading,
        coalesce(valid.valid_readings_24h, 0) as valid_readings_24h,
        -- 96 slots in 24 hours, 672 in 7 days.
        round(coalesce(valid.valid_readings_24h, 0) / 96, 4) as coverage_rate_24h,
        round(coalesce(valid.valid_readings_7d, 0) / 672, 4) as coverage_rate_7d,
        coalesce(flagged.flagged_readings_24h, 0) as flagged_readings_24h,
        coalesce(flagged.flagged_readings_7d, 0) as flagged_readings_7d,
        coalesce(flagged.flagged_readings_total, 0) as flagged_readings_total,
        coalesce(longest_gap.longest_gap_hours, 0) as longest_gap_hours
    from {{ ref('stg_stations') }} as stations
    cross join snapshot
    left join valid on valid.station_id = stations.station_id
    left join flagged on flagged.station_id = stations.station_id
    left join longest_gap on longest_gap.station_id = stations.station_id

),

classified as (

    select
        *,
        case
            when hours_since_latest_valid_reading is null or hours_since_latest_valid_reading > 24
                then 1
            when hours_since_latest_valid_reading > {{ stale_after_hours }} then 2
            when flagged_readings_24h > 0 then 3
            when coverage_rate_24h < {{ low_coverage_below }} then 4
            else 5
        end as status_rank
    from measured

)

select
    * except (status_rank),
    if(status_rank < 5, 'Check station', 'OK') as status,
    -- The reason carries the measured value next to its limit.
    case status_rank
        when 1 then 'No valid reading in the 24 h before collection'
        when 2 then concat(
            'Latest valid reading ', format('%.1f', hours_since_latest_valid_reading),
            ' h before collection (limit {{ stale_after_hours }} h)'
        )
        when 3 then concat(
            cast(flagged_readings_24h as string),
            ' flagged reading(s) of -50 °C in the 24 h before collection'
        )
        when 4 then concat(
            format('%.0f', coverage_rate_24h * 100),
            '% of expected readings in the 24 h before collection (limit {{ (low_coverage_below * 100) | int }}%)'
        )
        else 'Reporting normally'
    end as status_reason,
    status_rank < 5 as needs_check,
    status_rank
from classified
