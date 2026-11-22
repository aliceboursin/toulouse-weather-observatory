-- Default reliability metrics: forecast error by station and lead time, for
-- forecasts issued 1 to 7 days ahead. Lead 0 is in current_run_reference_error.
--
-- The station's coverage is repeated on each row so that a score can be read
-- next to the number of hours it rests on.

with scores as (

    select
        station_id,
        lead_days,
        count(error_c) as valid_comparisons,
        round(avg(absolute_error_c), 3) as mean_absolute_error_c,
        round(avg(error_c), 3) as mean_signed_error_c
    from {{ ref('forecast_comparison') }}
    where forecast_kind = 'forecast'
    group by station_id, lead_days

)

select
    scores.station_id,
    stations.station_label,
    scores.lead_days,
    scores.valid_comparisons,
    scores.mean_absolute_error_c,
    scores.mean_signed_error_c,
    coverage.non_future_hours,
    coverage.observation_coverage_rate
from scores
left join {{ ref('stg_stations') }} as stations
    on stations.station_id = scores.station_id
left join {{ ref('station_observation_coverage') }} as coverage
    on coverage.station_id = scores.station_id
