with source as (

    select * from {{ source('raw', 'stations') }}

),

named as (

    select
        *,
        -- The source has no display name, so one is derived from the dataset
        -- id: '11-station-meteo-toulouse-soupetard' -> 'Toulouse Soupetard'.
        initcap(replace(regexp_replace(dataset_id, r'^\d+-station-meteo-', ''), '-', ' '))
            as station_name
    from source

)

select
    station_id,
    station_name,
    -- The zero-padded number keeps labels in station order when sorted.
    concat(lpad(cast(station_id as string), 2, '0'), ' - ', station_name) as station_label,
    city,
    latitude,
    longitude,
    altitude_m
from named
