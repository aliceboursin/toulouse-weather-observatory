-- Default reliability metrics: forecast error by station and lead time, for
-- forecasts issued 1 to 7 days ahead. Lead 0 is in current_run_reference_error.

select
    station_id,
    lead_days,
    count(error_c) as valid_comparisons,
    round(avg(absolute_error_c), 3) as mean_absolute_error_c,
    round(avg(error_c), 3) as mean_signed_error_c
from {{ ref('forecast_comparison') }}
where forecast_kind = 'forecast'
group by station_id, lead_days
