-- Every forecast row with the observation it is compared against. Forecasts
-- with no usable observation are kept, with a null error.

with forecasts as (

    select * from {{ ref('stg_forecasts') }}

),

observations as (

    select * from {{ ref('int_station_hour_observations') }}

)

select
    forecasts.station_id,
    forecasts.valid_at,
    -- Toulouse local time, for date and hour filters.
    observations.valid_at_local,
    observations.valid_date_local,
    observations.valid_hour_local,
    forecasts.lead_days,
    forecasts.forecast_kind,
    forecasts.forecast_temperature_c,
    observations.observed_temperature_c,
    observations.observation_method,
    observations.observation_status,
    round(forecasts.forecast_temperature_c - observations.observed_temperature_c, 3)
        as error_c,
    round(abs(forecasts.forecast_temperature_c - observations.observed_temperature_c), 3)
        as absolute_error_c
from forecasts
left join observations
    on observations.station_id = forecasts.station_id
    and observations.valid_at = forecasts.valid_at
