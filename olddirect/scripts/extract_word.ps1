param(
    [string]$InputRoot = (Join-Path (Get-Location) 'resource'),
    [string]$OutputRoot = (Join-Path (Get-Location) 'data\word_text')
)
$ErrorActionPreference = 'Stop'
$monthly = [char]0x6708 + [char]0x8003
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
try {
    $root = (Resolve-Path $InputRoot).Path
    Get-ChildItem -LiteralPath $InputRoot -Recurse -File -Include *.doc,*.docx |
        Where-Object { $_.FullName -notmatch $monthly } |
        ForEach-Object {
            $relative = $_.FullName.Substring($root.Length).TrimStart('\')
            $target = Join-Path $OutputRoot ($relative + '.txt')
            New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
            $doc = $null
            try {
                $doc = $word.Documents.Open($_.FullName, $false, $true, $false)
                [System.IO.File]::WriteAllText($target, $doc.Content.Text, (New-Object System.Text.UTF8Encoding($false)))
            } finally {
                if ($null -ne $doc) { $doc.Close($false) }
            }
        }
} finally {
    $word.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null
}
