# Makima v7.1 — Build Gate Verification
# Checks all required tools before building.

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Makima v7.1 — Build Gate Check" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

$failed = $false

# Python 3.10+
Write-Host "`n[1/6] Python..." -NoNewline
try {
    $pyVer = python --version 2>&1
    if ($pyVer -match "3\.(1[0-9]|[2-9][0-9])") {
        Write-Host " OK ($pyVer)" -ForegroundColor Green
    } else {
        Write-Host " WARN: $pyVer (need 3.10+)" -ForegroundColor Yellow
    }
} catch {
    Write-Host " MISSING" -ForegroundColor Red
    $failed = $true
}

# Rust / Cargo
Write-Host "[2/6] Rust..." -NoNewline
try {
    $rustVer = rustc --version 2>&1
    Write-Host " OK ($rustVer)" -ForegroundColor Green
} catch {
    Write-Host " MISSING (install from rustup.rs)" -ForegroundColor Red
    $failed = $true
}

# Maturin
Write-Host "[3/6] Maturin..." -NoNewline
try {
    $matVer = maturin --version 2>&1
    Write-Host " OK ($matVer)" -ForegroundColor Green
} catch {
    Write-Host " MISSING (run: cargo install maturin)" -ForegroundColor Yellow
}

# Node.js
Write-Host "[4/6] Node.js..." -NoNewline
try {
    $nodeVer = node --version 2>&1
    Write-Host " OK ($nodeVer)" -ForegroundColor Green
} catch {
    Write-Host " MISSING (needed for Electron UI)" -ForegroundColor Yellow
}

# Protoc
Write-Host "[5/6] Protoc..." -NoNewline
try {
    $protocVer = protoc --version 2>&1
    Write-Host " OK ($protocVer)" -ForegroundColor Green
} catch {
    Write-Host " MISSING (optional, proto files pre-built)" -ForegroundColor Yellow
}

# Ollama
Write-Host "[6/6] Ollama..." -NoNewline
try {
    $ollamaVer = ollama --version 2>&1
    Write-Host " OK ($ollamaVer)" -ForegroundColor Green
} catch {
    Write-Host " MISSING (install from ollama.com)" -ForegroundColor Yellow
}

# Python deps
Write-Host "`n[Pip] Checking Python dependencies..." -ForegroundColor Cyan
$required = @("fastapi", "uvicorn", "pyyaml", "httpx")
foreach ($pkg in $required) {
    $check = pip show $pkg 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  $pkg OK" -ForegroundColor Green
    } else {
        Write-Host "  $pkg MISSING — run: pip install $pkg" -ForegroundColor Yellow
    }
}

Write-Host "`n========================================" -ForegroundColor Cyan
if ($failed) {
    Write-Host "BUILD GATE: FAILED — fix red items above" -ForegroundColor Red
    exit 1
} else {
    Write-Host "BUILD GATE: PASSED" -ForegroundColor Green
    exit 0
}
