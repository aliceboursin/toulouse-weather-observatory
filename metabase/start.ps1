# Starts the local Metabase on http://localhost:3000 and keeps running until
# you press Ctrl+C. Needs Java 21 and .metabase\metabase.jar (see README).
#
# Also serves the interactive map page (.metabase\map) on
# http://localhost:3001, which the V2 dashboard shows in its Map tab.
#
# Metabase's own database (users, questions, dashboards, the BigQuery
# connection) is the H2 file .metabase\metabase.db.mv.db. The .metabase
# folder is ignored by Git.

$dataDir = Join-Path (Split-Path $PSScriptRoot -Parent) '.metabase'
if (-not (Test-Path (Join-Path $dataDir 'metabase.jar'))) {
    Write-Error "Missing $dataDir\metabase.jar. Download it from https://downloads.metabase.com/latest/metabase.jar"
    exit 1
}

# Map page: jwebserver is the static file server shipped with the JDK.
$mapDir = Join-Path $dataDir 'map'
$mapServer = $null
if (Test-Path (Join-Path $mapDir 'index.html')) {
    $mapServer = Start-Process jwebserver -ArgumentList '-b', '127.0.0.1', '-p', '3001', '-d', "`"$mapDir`"" `
        -NoNewWindow -PassThru -RedirectStandardOutput (Join-Path $dataDir 'map-server.log') `
        -RedirectStandardError (Join-Path $dataDir 'map-server.err.log')
} else {
    Write-Warning "No map page yet. Run: uv run python metabase/build_map.py"
}

$env:MB_DB_TYPE = 'h2'
$env:MB_DB_FILE = Join-Path $dataDir 'metabase.db'
$env:MB_JETTY_HOST = '127.0.0.1'   # reachable from this machine only
$env:MB_JETTY_PORT = '3000'
$env:MB_ANON_TRACKING_ENABLED = 'false'
$env:MB_CHECK_FOR_UPDATES = 'false'

try {
    Set-Location $dataDir
    java -jar metabase.jar
} finally {
    if ($mapServer -and -not $mapServer.HasExited) { Stop-Process -Id $mapServer.Id -Force }
}
