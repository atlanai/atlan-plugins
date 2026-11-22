#!/usr/bin/env bash
# A ./reports folder as a team leaves one: a Tableau workbook and two saved SQL files, all on
# the flight-legs table.
set -euo pipefail
mkdir -p reports
cat > reports/fleet-punctuality.twb <<'XML'
<?xml version='1.0' encoding='utf-8' ?>
<workbook version='18.1'>
  <datasources>
    <datasource caption='Flight legs' name='federated.legs'>
      <connection class='snowflake' dbname='OPS' schema='FLIGHTS'>
        <relation name='LEGS' table='[FLIGHTS].[LEGS]' type='table' />
      </connection>
      <column caption='On-time departures' datatype='real' name='[Calculation_1]' role='measure' type='quantitative'>
        <calculation class='tableau' formula='SUM(IF [DEP_DELAY_MIN] &lt;= 15 THEN 1 ELSE 0 END) / COUNT([LEG_ID])' />
      </column>
      <column caption='Average delay (min)' datatype='real' name='[Calculation_2]' role='measure' type='quantitative'>
        <calculation class='tableau' formula='AVG([DEP_DELAY_MIN])' />
      </column>
      <column datatype='string' name='[STATION]' role='dimension' type='nominal' />
      <column datatype='date' name='[FLIGHT_DATE]' role='dimension' type='ordinal' />
    </datasource>
  </datasources>
  <worksheets>
    <worksheet name='On-time by station'>
      <table><view><datasources><datasource name='federated.legs' /></datasources></view>
        <rows>[federated.legs].[STATION]</rows><cols>[federated.legs].[Calculation_1]</cols></table>
    </worksheet>
    <worksheet name='Delay trend'>
      <table><view><datasources><datasource name='federated.legs' /></datasources></view>
        <rows>[federated.legs].[Calculation_2]</rows><cols>[federated.legs].[FLIGHT_DATE]</cols></table>
    </worksheet>
  </worksheets>
  <dashboards><dashboard name='Fleet punctuality'><zones><zone name='On-time by station' /><zone name='Delay trend' /></zones></dashboard></dashboards>
</workbook>
XML
cat > reports/turnaround.sql <<'SQL'
-- Weekly turnaround by station, from the ops review deck
SELECT station, DATE_TRUNC('week', flight_date) AS week, AVG(turnaround_min) AS avg_turnaround
FROM ops.flights.legs
WHERE cancelled = FALSE
GROUP BY 1, 2;
SQL
cat > reports/fuel_burn.sql <<'SQL'
SELECT aircraft_type, SUM(fuel_kg) / NULLIF(SUM(block_hours), 0) AS fuel_per_block_hour
FROM ops.flights.legs
GROUP BY aircraft_type;
SQL
