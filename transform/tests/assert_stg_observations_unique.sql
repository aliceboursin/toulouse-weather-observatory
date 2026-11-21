-- Fails if any station has more than one row for the same 15-minute slot.
select
    station_id,
    observed_at,
    count(*) as row_count
from {{ ref('stg_observations') }}
group by station_id, observed_at
having count(*) > 1
