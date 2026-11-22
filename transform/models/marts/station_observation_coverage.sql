-- Observation coverage rate per station: comparable target hours divided by
-- non-future target hours. Hours that were still in the future at ingestion
-- are reported separately and are not in the denominator, because no
-- observation could exist for them yet.

with station_hours as (

    select * from {{ ref('int_station_hour_observations') }}

),

counted as (

    select
        station_id,
        count(*) as target_hours,
        countif(observation_status = 'future_at_ingestion') as future_hours,
        countif(observation_status = 'observed') as comparable_hours,
        countif(observation_status = 'missing_observation') as missing_hours
    from station_hours
    group by station_id

)

select
    station_id,
    target_hours,
    future_hours,
    target_hours - future_hours as non_future_hours,
    comparable_hours,
    missing_hours,
    round(safe_divide(comparable_hours, target_hours - future_hours), 4)
        as observation_coverage_rate
from counted
