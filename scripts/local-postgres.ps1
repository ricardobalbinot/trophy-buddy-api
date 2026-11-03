param([ValidateSet('start', 'stop', 'status')][string]$Action = 'status')

# Manage only the existing user-local PostgreSQL cluster; credentials stay outside the repository.
$toolRoot = Join-Path $env:LOCALAPPDATA 'Programs\trophy-buddy-tools\postgresql'
$controlPath = Join-Path $toolRoot 'pgsql\bin\pg_ctl.exe'
$dataPath = Join-Path $toolRoot 'data'

if (-not (Test-Path -LiteralPath $controlPath)) {
    throw 'Local PostgreSQL binaries were not found. Use Docker Compose or an installed PostgreSQL server.'
}

if ($Action -eq 'start') {
    & $controlPath -D $dataPath -l (Join-Path $toolRoot 'server.log') -o '-h 127.0.0.1 -p 5433' start
} elseif ($Action -eq 'stop') {
    & $controlPath -D $dataPath -m fast stop
} else {
    & $controlPath -D $dataPath status
}

exit $LASTEXITCODE
