-- Error of the lead 0 values by station. Lead 0 is the most recent model run,
-- not a forecast issued in advance, so it is reported apart from
-- forecast_reliability as a reference.

with scores as (

    select
        station_id,
        lead_days,
        count(error_c) as valid_comparisons,
        round(avg(absolute_error_c), 3) as mean_absolute_error_c,
        round(avg(error_c), 3) as mean_signed_error_c
    from {{ ref('forecast_comparison') }}
    where forecast_kind = 'current_run_reference'
    group by station_id, lead_days

)

select
    scores.station_id,
    stations.station_label,
    scores.lead_days,
    scores.valid_comparisons,
    scores.mean_absolute_error_c,
    scores.mean_signed_error_c
from scores
left join {{ ref('stg_stations') }} as stations
    on stations.station_id = scores.station_id
