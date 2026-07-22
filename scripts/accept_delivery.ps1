param(
    [switch]$Full,
    [string]$ApiBaseUrl = "http://localhost:8000/api/v1",
    [string]$WebUrl = "http://localhost:5173",
    [string]$ElasticsearchUrl = "http://localhost:9200",
    [int]$MySqlPort = 3307,
    [string]$Output
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python virtual environment not found: $python"
}

$mode = if ($Full) { "full" } else { "smoke" }
if (-not $Output) {
    $Output = Join-Path $root "evals\delivery\results\${mode}_latest.json"
}
$databaseUrl = "mysql+aiomysql://mootcourt:change-me@127.0.0.1:$MySqlPort/mootcourt"
$arguments = @(
    "-m", "mootcourt.cli.accept_delivery",
    "--api-base-url", $ApiBaseUrl,
    "--web-url", $WebUrl,
    "--elasticsearch-url", $ElasticsearchUrl,
    "--database-url", $databaseUrl,
    "--output", $Output
)
if ($Full) {
    $arguments += "--full"
}

Push-Location (Join-Path $root "backend")
try {
    # Runner 只读取基础设施状态并通过公开 API 创建独立验收会话，不操作 Docker 数据卷。
    & $python @arguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
