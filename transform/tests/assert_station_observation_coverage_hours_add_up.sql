-- Fails if a station's target hours are not exactly its future, missing and
-- comparable hours, or if its coverage rate is outside 0 to 1.
select *
from {{ ref('station_observation_coverage') }}
where target_hours != future_hours + missing_hours + comparable_hours
    or non_future_hours != missing_hours + comparable_hours
    or observation_coverage_rate < 0
    or observation_coverage_rate > 1
