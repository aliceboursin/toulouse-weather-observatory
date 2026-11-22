-- Fails if a station and target hour has fewer than the 8 lead times (0 to 7).
-- The ingestion skips null temperatures, so a missing forecast shows up as a
-- missing lead here.
--
-- This only checks station-hours that are present in the data. It cannot
-- detect a station-hour that is missing for every lead time.
select
    station_id,
    valid_at,
    count(distinct lead_days) as lead_count
from {{ ref('stg_forecasts') }}
group by station_id, valid_at
having count(distinct lead_days) != 8
