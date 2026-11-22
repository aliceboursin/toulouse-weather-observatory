-- Fails if, for any station, the missing readings counted from gaps differ
-- from the missing readings counted per day. Both views describe the same
-- 15-minute slots, so the totals must agree.
with from_gaps as (

    select station_id, sum(missing_readings) as missing_readings
    from {{ ref('station_observation_gaps') }}
    group by station_id

),

from_days as (

    select station_id, sum(missing_readings) as missing_readings
    from {{ ref('station_daily_observations') }}
    group by station_id

)

select
    from_days.station_id,
    from_days.missing_readings as missing_from_days,
    from_gaps.missing_readings as missing_from_gaps
from from_days
left join from_gaps using (station_id)
where from_days.missing_readings != coalesce(from_gaps.missing_readings, 0)
