-- Error of the lead 0 values by station. Lead 0 is the most recent model run,
-- not a forecast issued in advance, so it is reported apart from
-- forecast_reliability as a reference.

select
    station_id,
    lead_days,
    count(error_c) as valid_comparisons,
    round(avg(absolute_error_c), 3) as mean_absolute_error_c,
    round(avg(error_c), 3) as mean_signed_error_c
from {{ ref('forecast_comparison') }}
where forecast_kind = 'current_run_reference'
group by station_id, lead_days
