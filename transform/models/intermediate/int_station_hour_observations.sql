-- One row per station and forecast target hour, with the observation that
-- forecasts for that hour are compared against.
--
-- A forecast is the instantaneous temperature at time t. A station reading
-- timestamped t is the mean over the 15 minutes ending at t. The readings at
-- t and t + 15 minutes together cover t - 15 min to t + 15 min, which is
-- centred on t.

with target_hours as (

    select
        station_id,
        valid_at,
        max(ingested_at) as ingested_at
    from {{ ref('stg_forecasts') }}
    group by station_id, valid_at

),

observations as (

    select station_id, observed_at, temperature_c
    from {{ ref('stg_observations') }}

),

matched as (

    select
        target_hours.station_id,
        target_hours.valid_at,
        target_hours.ingested_at,
        at_t.temperature_c as observed_at_t_c,
        after_t.temperature_c as observed_at_t_plus_15_c
    from target_hours
    left join observations as at_t
        on at_t.station_id = target_hours.station_id
        and at_t.observed_at = target_hours.valid_at
    left join observations as after_t
        on after_t.station_id = target_hours.station_id
        and after_t.observed_at = timestamp_add(target_hours.valid_at, interval 15 minute)

)

select
    station_id,
    valid_at,
    observed_at_t_c,
    observed_at_t_plus_15_c,
    case
        when observed_at_t_c is not null and observed_at_t_plus_15_c is not null
            then round((observed_at_t_c + observed_at_t_plus_15_c) / 2, 3)
        else coalesce(observed_at_t_c, observed_at_t_plus_15_c)
    end as observed_temperature_c,
    case
        when observed_at_t_c is not null and observed_at_t_plus_15_c is not null
            then 'centered_30min'
        when observed_at_t_c is not null then 'preceding_15min'
        when observed_at_t_plus_15_c is not null then 'following_15min'
    end as observation_method,
    -- Without an observation, the hour is either still in the future or a
    -- gap in the station's data. The forecast ingestion time stands in for
    -- the observation cutoff, because both are fetched in the same run.
    case
        when observed_at_t_c is not null or observed_at_t_plus_15_c is not null
            then 'observed'
        when valid_at > ingested_at then 'future_at_ingestion'
        else 'missing_observation'
    end as observation_status
from matched
