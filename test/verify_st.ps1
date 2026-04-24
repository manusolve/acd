param(
  [string]$Mine       = '',
  [string]$BonaFide   = ''
)

Write-Host "`n=== 1. File sizes ===" -ForegroundColor Cyan
Get-Item $Mine, $BonaFide | Select Name, Length, Directory | Format-Table -AutoSize

Write-Host "`n=== 2. ST presence in YOUR export ===" -ForegroundColor Cyan
$mineTxt = Get-Content $Mine -Raw
"STContent elements : $(([regex]::Matches($mineTxt,'<STContent')).Count)"
"Line elements      : $(([regex]::Matches($mineTxt,'<Line ')).Count)"
"Type=""ST"" routines : $(([regex]::Matches($mineTxt,'Type="ST"')).Count)"
"Empty ST routines  : $(([regex]::Matches($mineTxt,'Type="ST"[^>]*/>|Type="ST"></Routine>|Type="ST"\s*>\s*</Routine>')).Count)"

Write-Host "`n=== 3. ST presence in BONA-FIDE Logix export ===" -ForegroundColor Cyan
$bfTxt = Get-Content $BonaFide -Raw
"STContent elements : $(([regex]::Matches($bfTxt,'<STContent')).Count)"
"Line elements      : $(([regex]::Matches($bfTxt,'<Line ')).Count)"
"Type=""ST"" routines : $(([regex]::Matches($bfTxt,'Type="ST"')).Count)"

Write-Host "`n=== 4. Distinctive source strings in YOUR export ===" -ForegroundColor Cyan
foreach ($s in '20251002','frontOffalLength','positionFullBack','// param','standard value for default') {
    $n = ([regex]::Matches($mineTxt,[regex]::Escape($s))).Count
    "{0,-35} : {1}" -f $s, $n
}

Write-Host "`n=== 5. Same strings in BONA-FIDE (reference) ===" -ForegroundColor Cyan
foreach ($s in '20251002','frontOffalLength','positionFullBack','// param','standard value for default') {
    $n = ([regex]::Matches($bfTxt,[regex]::Escape($s))).Count
    "{0,-35} : {1}" -f $s, $n
}

Write-Host "`n=== 6. Unresolved tag placeholders (@hex@) in YOUR export ===" -ForegroundColor Cyan
"YOUR      : $(([regex]::Matches($mineTxt,'@[0-9a-f]{8}@')).Count)"
"BONA-FIDE : $(([regex]::Matches($bfTxt  ,'@[0-9a-f]{8}@')).Count)  (should be 0)"

Write-Host "`n=== 7. Tabs preserved (source formatting) ===" -ForegroundColor Cyan
$mineBytes = [IO.File]::ReadAllBytes($Mine)
$bfBytes   = [IO.File]::ReadAllBytes($BonaFide)
"YOUR TAB bytes      : $(@($mineBytes | Where-Object {$_ -eq 9}).Count)"
"BONA-FIDE TAB bytes : $(@($bfBytes   | Where-Object {$_ -eq 9}).Count)"

Write-Host "`n=== 8. Routine names: BONA-FIDE vs YOURS ===" -ForegroundColor Cyan
$bfRoutines   = [regex]::Matches($bfTxt,  '<Routine\s+Name="([^"]+)"\s+Type="ST"') | ForEach-Object { $_.Groups[1].Value }
$mineRoutines = [regex]::Matches($mineTxt,'<Routine\s+Name="([^"]+)"\s+Type="ST"') | ForEach-Object { $_.Groups[1].Value }
"ST routines in BONA-FIDE : $($bfRoutines.Count)"
"ST routines in YOURS     : $($mineRoutines.Count)"
$missing = Compare-Object $bfRoutines $mineRoutines -PassThru
if ($missing) { Write-Host "Differences:" -ForegroundColor Yellow; $missing | ForEach-Object { "  $_" } }
else          { Write-Host "Routine name sets match" -ForegroundColor Green }

Write-Host "`n=== 9. Sample ST body from ONE named routine (YOURS) ===" -ForegroundColor Cyan
# Pick the first ST routine name from bona-fide and show what YOURS has under it
if ($bfRoutines) {
    $name = $bfRoutines[0]
    $pattern = "(?s)<Routine\s+Name=""$([regex]::Escape($name))""\s+Type=""ST"".*?</Routine>"
    $m = [regex]::Match($mineTxt, $pattern)
    if ($m.Success) {
        $body = $m.Value
        if ($body.Length -gt 1000) { $body = $body.Substring(0, 1000) + "`n... (truncated)" }
        "Routine '$name' in YOUR export:"
        $body
    } else { "Routine '$name' not found in YOURS" }
}