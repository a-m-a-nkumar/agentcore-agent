@@ -1,36 +1,178 @@
# Start Frontend Server
# Deluxe SDLC Frontend (from Azure DevOps)
# ====================================================================
# START FRONTEND SERVER
# Deluxe SDLC Frontend (React + Vite)
# ====================================================================

# Set execution policy for this session only
Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process -Force

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Starting Frontend Server..." -ForegroundColor Cyan
Write-Host "  Deluxe SDLC Frontend Server" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Navigate to the frontend directory
$FrontendPath = "C:\Users\ArushSingh\Desktop\FRONTEND1"
# Frontend directory path
$FrontendPath = "C:\Users\ArushSingh\Desktop\FRONTEND1_new"

# STEP 1 - Verify frontend directory
Write-Host "[1/5] Checking frontend directory..." -ForegroundColor Yellow
if (-Not (Test-Path $FrontendPath)) {
    Write-Host "  [ERROR] Frontend directory not found!" -ForegroundColor Red
    Write-Host "  Expected path: $FrontendPath" -ForegroundColor Yellow
    exit 1
}
Write-Host "  [OK] Frontend directory found" -ForegroundColor Green
Write-Host "  Path: $FrontendPath" -ForegroundColor Gray
Write-Host ""

# STEP 2 - Navigate to frontend directory
Write-Host "[2/5] Navigating to frontend directory..." -ForegroundColor Yellow
Set-Location -Path $FrontendPath
Write-Host "  [OK] Current directory: $(Get-Location)" -ForegroundColor Green
Write-Host ""

Write-Host "Current Directory: $FrontendPath" -ForegroundColor Yellow
# STEP 3 - Check Node.js and npm
Write-Host "[3/5] Checking Node.js and npm..." -ForegroundColor Yellow

$nodeVersion = node --version 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  [OK] Node.js : $nodeVersion" -ForegroundColor Green
} else {
    Write-Host "  [ERROR] Node.js not found! Install from: https://nodejs.org/" -ForegroundColor Red
    exit 1
}

$npmVersion = npm --version 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  [OK] npm     : v$npmVersion" -ForegroundColor Green
} else {
    Write-Host "  [ERROR] npm not found!" -ForegroundColor Red
    exit 1
}
Write-Host ""

# Check if node_modules exists
if (-not (Test-Path "node_modules")) {
    Write-Host "[WARNING] node_modules not found!" -ForegroundColor Yellow
    Write-Host "Installing dependencies..." -ForegroundColor Yellow
    Write-Host ""
    npm install
# STEP 4 - Per-package dependency audit
Write-Host "[4/5] Auditing packages from package.json..." -ForegroundColor Yellow
Write-Host ""

# Read package.json
$pkgJsonPath = Join-Path $FrontendPath "package.json"
if (-Not (Test-Path $pkgJsonPath)) {
    Write-Host "  [ERROR] package.json not found at: $pkgJsonPath" -ForegroundColor Red
    exit 1
}
$pkgJson = Get-Content $pkgJsonPath -Raw | ConvertFrom-Json

# Collect all package names from dependencies and devDependencies
$allPackages = @{}

if ($pkgJson.dependencies) {
    $pkgJson.dependencies.PSObject.Properties | ForEach-Object {
        $allPackages[$_.Name] = @{ Version = $_.Value; Type = "dep" }
    }
}
if ($pkgJson.devDependencies) {
    $pkgJson.devDependencies.PSObject.Properties | ForEach-Object {
        $allPackages[$_.Name] = @{ Version = $_.Value; Type = "dev" }
    }
}

$installedList = [System.Collections.Generic.List[string]]::new()
$missingList   = [System.Collections.Generic.List[string]]::new()

Write-Host ("  {0,-45} {1,-12} {2}" -f "Package", "Required", "Status") -ForegroundColor DarkCyan
Write-Host ("  {0,-45} {1,-12} {2}" -f "-------", "--------", "------") -ForegroundColor DarkGray

foreach ($pkg in ($allPackages.Keys | Sort-Object)) {
    $info       = $allPackages[$pkg]
    $reqVersion = $info.Version
    $pkgType    = $info.Type
    $moduleDir  = Join-Path $FrontendPath "node_modules\$pkg"
    $typeLabel  = if ($pkgType -eq "dev") { "(dev)" } else { "     " }

    if (Test-Path $moduleDir) {
        $modPkgJson = Join-Path $moduleDir "package.json"
        if (Test-Path $modPkgJson) {
            $modPkg       = Get-Content $modPkgJson -Raw | ConvertFrom-Json
            $installedVer = $modPkg.version
            $statusText   = "[OK]  v$installedVer"
        } else {
            $statusText   = "[OK]  (found)"
        }
        $installedList.Add($pkg) | Out-Null
        Write-Host ("  {0,-45} {1,-12} " -f "$pkg $typeLabel", $reqVersion) -NoNewline
        Write-Host $statusText -ForegroundColor Green
    } else {
        $missingList.Add($pkg) | Out-Null
        Write-Host ("  {0,-45} {1,-12} " -f "$pkg $typeLabel", $reqVersion) -NoNewline
        Write-Host "[MISSING]" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "  --------------------------------------------------------" -ForegroundColor DarkGray
Write-Host ("  Total packages in package.json : {0}" -f $allPackages.Count) -ForegroundColor White
Write-Host ("  Installed                       : {0}" -f $installedList.Count) -ForegroundColor Green

if ($missingList.Count -gt 0) {
    Write-Host ("  Missing                         : {0}" -f $missingList.Count) -ForegroundColor Red
} else {
    Write-Host ("  Missing                         : {0}" -f $missingList.Count) -ForegroundColor Green
}
Write-Host "  --------------------------------------------------------" -ForegroundColor DarkGray
Write-Host ""

# Install missing packages if any
if ($missingList.Count -gt 0) {
    Write-Host "  [ACTION] Installing missing packages..." -ForegroundColor Yellow
    Write-Host ""

    foreach ($missing in $missingList) {
        Write-Host "  --> Installing: $missing" -ForegroundColor Cyan
        $isDevPkg = $allPackages[$missing].Type -eq "dev"

        if ($isDevPkg) {
            npm install --save-dev $missing
        } else {
            npm install $missing
        }

        if ($LASTEXITCODE -ne 0) {
            Write-Host "  [WARN] Failed to install $missing - continuing..." -ForegroundColor Yellow
        } else {
            Write-Host "  [OK]  $missing installed successfully" -ForegroundColor Green
        }
        Write-Host ""
    }

    Write-Host "  [DONE] All missing packages installed." -ForegroundColor Green
} else {
    Write-Host "  [OK] All packages already installed. Nothing to do." -ForegroundColor Green
}
Write-Host ""

Write-Host "Starting Vite development server..." -ForegroundColor Cyan
Write-Host "Frontend will be available at: http://localhost:5173" -ForegroundColor Green
# STEP 5 - Check .env and start dev server
Write-Host "[5/5] Checking environment file..." -ForegroundColor Yellow
if (Test-Path ".env") {
    Write-Host "  [OK] .env file found" -ForegroundColor Green
} else {
    Write-Host "  [WARN] .env file not found - frontend may not connect to backend." -ForegroundColor Yellow
}
Write-Host ""

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Starting Vite Development Server..." -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Frontend URL : http://localhost:5173" -ForegroundColor Cyan
Write-Host ""
Write-Host "Press Ctrl+C to stop the server" -ForegroundColor Yellow
Write-Host "  Press Ctrl+C to stop the server" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Start the development server
npm run dev

Write-Host ""
Write-Host "[INFO] Frontend server stopped." -ForegroundColor Yellow
Write-Host ""