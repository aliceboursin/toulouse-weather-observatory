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
    counted.station_id,
    stations.station_label,
    counted.target_hours,
    counted.future_hours,
    counted.target_hours - counted.future_hours as non_future_hours,
    counted.comparable_hours,
    counted.missing_hours,
    round(safe_divide(counted.comparable_hours, counted.target_hours - counted.future_hours), 4)
        as observation_coverage_rate
from counted
left join {{ ref('stg_stations') }} as stations
    on stations.station_id = counted.station_id
