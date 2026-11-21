with source as (

    select * from {{ source('raw', 'observations') }}

),

without_sentinel as (

    -- Readings of exactly -50 °C were flagged during profiling and are
    -- excluded.
    select *
    from source
    where temperature_c != -50

),

deduplicated as (

    -- The source API sometimes publishes several readings for the same
    -- station and 15-minute slot, with slightly different values and nothing
    -- to say which is right, so we average them. reading_count is the number
    -- of valid readings that contribute to the row after the filter above.
    select
        station_id,
        observed_at,
        round(avg(temperature_c), 2) as temperature_c,
        count(*) as reading_count
    from without_sentinel
    group by station_id, observed_at

)

select * from deduplicated
