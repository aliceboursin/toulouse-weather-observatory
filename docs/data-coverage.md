# Data coverage

Back to the [README](../README.md).

The data covers 1 April to 19 November 2026 (UTC): 151,264 rows in `raw.observations` and 447,360 in `raw.forecasts`.

## Forecasts

Forecasts are complete. Open-Meteo returned a value for every station, hour and lead time (J0 to J7) over the whole period: 10 stations × 5,592 hours × 8 lead times = 447,360 records.

## Observations

Observations have gaps in the source. The table gives the comparison coverage per station and month: the share of past target hours with an observed reference, that is, a valid reading at the hour or 15 minutes later (see [How forecasts are compared with observations](../README.md#how-forecasts-are-compared-with-observations)).

| Station | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Whole period |
|---|---|---|---|---|---|---|---|---|---|
| 02 Marengo | 99% | 100% | 100% | 99% | 100% | 100% | 99% | 100% | 100% |
| 11 Soupetard | 2% | 5% | 0% | 0% | 0% | 0% | 0% | 1% | 1% |
| 12 Montaudran | 98% | 93% | 94% | 98% | 99% | 95% | 97% | 98% | 97% |
| 13 Pech David | 99% | 100% | 100% | 99% | 100% | 100% | 100% | 100% | 100% |
| 23 Pibrac | 0% | 10% | 82% | 83% | 82% | 84% | 84% | 97% | 64% |
| 33 Saint-Jory | 3% | 8% | 4% | 3% | 3% | 9% | 13% | 17% | 7% |
| 37 Paul Sabatier | 100% | 100% | 100% | 97% | 100% | 100% | 97% | 99% | 99% |
| 42 Compans | 83% | 82% | 89% | 92% | 90% | 88% | 88% | 77% | 87% |
| 58 Fondeyre | 46% | 50% | 44% | 26% | 44% | 63% | 47% | 57% | 47% |
| 62 Maourine | 97% | 97% | 93% | 96% | 96% | 92% | 90% | 97% | 95% |

- Soupetard (11) and Saint-Jory (33) rarely report. Their scores rest on 62 and 387 comparable hours, against more than 5,000 for the best stations.
- Pibrac (23) has almost no reading before late May.
- Fondeyre (58) reports about half the time.

Over the whole period, 38,744 station-hours have an observed reference, 17,056 have none, and 120 were still in the future when the data was collected on 19 November.

## Readings removed or merged before the comparison

- 918 readings are exactly -50 °C, a value flagged as invalid during data profiling. The next lowest reading in the data is 1.8 °C. `stg_observations` removes them, and the Station monitoring tab counts them per station and day.
- 101 station and timestamp pairs are published twice, sometimes with different temperatures. `stg_observations` averages them.

The raw tables keep every row as the source published it.
