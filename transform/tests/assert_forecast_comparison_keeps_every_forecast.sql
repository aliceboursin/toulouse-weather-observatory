-- Fails if the join to observations dropped or multiplied forecast rows:
-- forecast_comparison must have exactly as many rows as stg_forecasts.
with counts as (

    select
        (select count(*) from {{ ref('stg_forecasts') }}) as forecast_rows,
        (select count(*) from {{ ref('forecast_comparison') }}) as comparison_rows

)

select *
from counts
where forecast_rows != comparison_rows
