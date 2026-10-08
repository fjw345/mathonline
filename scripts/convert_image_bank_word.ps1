param([string]$OutputRoot = (Join-Path (Get-Location) 'data\image_bank'))
$ErrorActionPreference = 'Stop'
$inventory = Get-Content -Raw -Encoding UTF8 (Join-Path $OutputRoot 'inventory.json') | ConvertFrom-Json
$pdfRoot = Join-Path $OutputRoot 'converted'
New-Item -ItemType Directory -Force -Path $pdfRoot | Out-Null
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
$word.AutomationSecurity = 3
$results = @()
try {
    foreach ($item in $inventory.files | Where-Object { $_.format -in @('doc','docx') }) {
        $source = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $item.path))
        $target = Join-Path $pdfRoot ($item.id + '.pdf')
        $hashPath = $target + '.sha256'
        $doc = $null
        $result = @{ id=$item.id; source=$item.path; pdf=('data/image_bank/converted/' + $item.id + '.pdf'); original_sha256=$item.sha256 }
        try {
            if ((Test-Path -LiteralPath $target) -and (Test-Path -LiteralPath $hashPath) -and ((Get-Content -Raw $hashPath).Trim() -eq $item.sha256)) {
                $result.status = 'cached'
            } else {
                $doc = $word.Documents.Open($source, $false, $true, $false)
                $doc.ExportAsFixedFormat($target, 17)
                [System.IO.File]::WriteAllText($hashPath, $item.sha256)
                $result.status = 'converted'
            }
        } catch {
            $result.status = 'conversion_error'
            $result.error = $_.Exception.Message
        } finally {
            if ($null -ne $doc) {
                $doc.Close($false)
                [System.Runtime.InteropServices.Marshal]::ReleaseComObject($doc) | Out-Null
            }
        }
        $results += $result
        $reportPath = Join-Path $OutputRoot 'word_conversion.json'
        [System.IO.File]::WriteAllText($reportPath, (ConvertTo-Json -Depth 10 -InputObject @($results)), [System.Text.UTF8Encoding]::new($false))
        Write-Output ('WORD ' + $results.Count + '/54 ' + $result.status + ' ' + $item.name)
    }
} finally {
    $word.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null
}
