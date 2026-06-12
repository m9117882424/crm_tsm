Copy-Item .env.example .env -Force
$bytes = New-Object byte[] 64
[Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
$key = ($bytes | ForEach-Object { $_.ToString("x2") }) -join ""
(Get-Content .env) -replace "CHANGE_ME_GENERATE_WITH_OPENSSL_RAND_HEX_64", $key | Set-Content .env
Write-Host ".env created"
