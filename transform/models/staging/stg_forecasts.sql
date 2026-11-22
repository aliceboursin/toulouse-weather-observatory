with source as (

    select * from {{ source('raw', 'forecasts') }}

)

select
    station_id,
    valid_at,
    lead_days,
    -- Lead 0 is the most recent model run, not a forecast issued in
    -- advance, so it is labelled to keep it out of the default metrics.
    case
        when lead_days = 0 then 'current_run_reference'
        else 'forecast'
    end as forecast_kind,
    temperature_c as forecast_temperature_c,
    ingested_at
from source
