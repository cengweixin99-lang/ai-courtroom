param(
    [string]$OllamaBaseUrl = "http://127.0.0.1:11434/v1",
    [string]$ElasticsearchUrl = "http://127.0.0.1:9200"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$pythonScripts = Join-Path $repositoryRoot ".venv\Scripts"
$modelId = "790764642607"
$embeddingVersion = "ollama-bge-m3-790764642607-1024-v1"

$installedModel = ollama list | Select-String -Pattern "^bge-m3:latest\s+$modelId\s+"
if (-not $installedModel) {
    throw "Required Ollama model bge-m3:latest with ID $modelId is not installed."
}

$databaseLine = Get-Content -Encoding utf8 (Join-Path $repositoryRoot ".env") |
    Where-Object { $_ -match '^DATABASE_URL=' } |
    Select-Object -First 1
if (-not $databaseLine) {
    throw "DATABASE_URL is missing from .env."
}

# 仅为当前脚本进程切换到宿主机映射端口，不修改或输出 .env 中的数据库凭据。
$databaseLine = [string]$databaseLine
$separatorIndex = $databaseLine.IndexOf('=')
if ($separatorIndex -lt 1) {
    throw "DATABASE_URL in .env is invalid."
}
$databaseUrl = $databaseLine.Substring($separatorIndex + 1)
$env:DATABASE_URL = $databaseUrl.Replace('@mysql:3306', '@127.0.0.1:3307')
if ($env:DATABASE_URL -notmatch '@127\.0\.0\.1:3307/') {
    throw "Host-side DATABASE_URL must resolve to the mapped MySQL port 127.0.0.1:3307."
}
$env:ELASTICSEARCH_URL = $ElasticsearchUrl
$env:LEGAL_EMBEDDING_ENABLED = "true"
$env:LEGAL_EMBEDDING_PROVIDER = "openai-compatible"
$env:LEGAL_EMBEDDING_MODEL = "bge-m3"
$env:LEGAL_EMBEDDING_API_KEY = "ollama-local"
$env:LEGAL_EMBEDDING_BASE_URL = $OllamaBaseUrl
$env:LEGAL_EMBEDDING_VERSION = $embeddingVersion
$env:LEGAL_EMBEDDING_DIMENSIONS = "1024"
$env:LEGAL_EMBEDDING_TIMEOUT_SECONDS = "300"
$env:LEGAL_VECTOR_SIMILARITY_THRESHOLD = "0.78"

Push-Location $repositoryRoot
try {
    & (Join-Path $pythonScripts "mootcourt-index-legal.exe") `
        "knowledge\legal\source_manifest.json"
    if ($LASTEXITCODE -ne 0) {
        throw "Legal vector indexing failed with exit code $LASTEXITCODE."
    }
    & (Join-Path $pythonScripts "mootcourt-eval-legal.exe") `
        "evals\legal_rag\bm25_baseline_cases.json" `
        --output "evals\legal_rag\results\hybrid_rrf_report.json"
    if ($LASTEXITCODE -ne 0) {
        throw "Hybrid legal Eval failed with exit code $LASTEXITCODE."
    }
    & (Join-Path $pythonScripts "mootcourt-compare-legal-evals.exe") `
        "evals\legal_rag\results\bm25_baseline_report.json" `
        "evals\legal_rag\results\hybrid_rrf_report.json" `
        --policy "evals\legal_rag\hybrid_admission_policy.json" `
        --output "evals\legal_rag\results\hybrid_vs_bm25_comparison.json"
    if ($LASTEXITCODE -ne 0) {
        throw "Hybrid admission comparison failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
