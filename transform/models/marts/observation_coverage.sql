-- How many target hours each station has in each coverage category. Each
-- station and target hour is counted once, whatever the number of lead times.
--
-- share_of_station_hours is a distribution across categories: its denominator
-- is all target hours, future hours included. The coverage rate, which
-- excludes future hours, is in station_observation_coverage.

with counted as (

    select
        station_id,
        coalesce(observation_method, observation_status) as coverage_category,
        count(*) as station_hours
    from {{ ref('int_station_hour_observations') }}
    group by station_id, coverage_category

)

select
    counted.station_id,
    stations.station_label,
    counted.coverage_category,
    counted.station_hours,
    round(counted.station_hours / sum(counted.station_hours) over (partition by counted.station_id), 4)
        as share_of_station_hours
from counted
left join {{ ref('stg_stations') }} as stations
    on stations.station_id = counted.station_id
