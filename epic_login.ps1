$ErrorActionPreference = "Stop"

# Load .env file if present
$envFile = Join-Path $PSScriptRoot ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([^#][^=]+?)\s*=\s*(.+?)\s*$') {
            [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], "Process")
        }
    }
}

$clientId = $env:EPIC_CLIENT_ID
$clientSecret = $env:EPIC_CLIENT_SECRET
if (-not $clientId -or -not $clientSecret) {
    Write-Error "EPIC_CLIENT_ID and EPIC_CLIENT_SECRET must be set (in .env or environment)."
    exit 1
}
$tokenUrl = "https://account-public-service-prod.ol.epicgames.com/account/api/oauth/token"
$loginUrl = "https://www.epicgames.com/id/login"
$redirectUrl = "https://www.epicgames.com/id/api/redirect?clientId=$clientId&responseType=code"

Write-Host "Epic Login Helper"
Write-Host "1) Log in to epicgames.com in your browser"
Write-Host "2) After login, continue here to open the authorization page"
Write-Host "3) Copy the authorizationCode from the browser"
Write-Host ""
Write-Host "Login URL:"
Write-Host "   $loginUrl"
Write-Host ""
Write-Host "Authorization URL:"
Write-Host "   $redirectUrl"
Write-Host ""

try {
    Start-Process $loginUrl | Out-Null
} catch {
    # Optional only; continue if opening browser fails
}

Read-Host "Press Enter after you have logged in to Epic Games"

try {
    Start-Process $redirectUrl | Out-Null
} catch {
    # Optional only; continue if opening browser fails
}

$authorizationCode = Read-Host "authorizationCode"
if ([string]::IsNullOrWhiteSpace($authorizationCode)) {
    Write-Error "authorizationCode is empty."
    exit 1
}

$basicBytes = [Text.Encoding]::UTF8.GetBytes("${clientId}:${clientSecret}")
$basicAuth = [Convert]::ToBase64String($basicBytes)
$headers = @{
    Authorization = "Basic $basicAuth"
    "Content-Type" = "application/x-www-form-urlencoded"
    Accept = "application/json"
}

$body = "grant_type=authorization_code&code=$([Uri]::EscapeDataString($authorizationCode))&token_type=eg1"

try {
    $response = Invoke-RestMethod -Method Post -Uri $tokenUrl -Headers $headers -Body $body
} catch {
    Write-Host "Token exchange failed." -ForegroundColor Red
    if ($_.Exception.Response -and $_.Exception.Response.GetResponseStream()) {
        $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
        $errorBody = $reader.ReadToEnd()
        if ($errorBody) {
            Write-Host $errorBody -ForegroundColor Red
        }
    }
    exit 1
}

if (-not $response.refresh_token) {
    Write-Error "No refresh_token found in response."
    exit 1
}

$env:EPIC_REFRESH_TOKEN = [string]$response.refresh_token

Write-Host ""
Write-Host "EPIC_REFRESH_TOKEN has been set in the current terminal." -ForegroundColor Green
Write-Host "You can now run:" -ForegroundColor Green
Write-Host "  uv run .\get_data.py" -ForegroundColor Green
Write-Host "  uv run .\missing_items.py" -ForegroundColor Green
Write-Host "  uv run .\squads.py" -ForegroundColor Green
