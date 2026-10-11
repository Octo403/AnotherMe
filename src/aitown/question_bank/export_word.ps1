param(
    [Parameter(Mandatory=$true)][string]$InputPath,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [string]$ProcessRecord
)
$ErrorActionPreference = 'Stop'
$wordApp = $null
$wordDoc = $null
$oldSecurity = $null
$oldLinks = $null
$oldFields = $null
$oldPrintLinks = $null
$owned = $false
try {
    $existingWord = @(Get-Process -Name WINWORD -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
    $wordApp = New-Object -ComObject Word.Application
    $newWord = @(Get-Process -Name WINWORD -ErrorAction SilentlyContinue | Where-Object { $existingWord -notcontains $_.Id })
    if ($newWord.Count -ne 1 -or $wordApp.Documents.Count -ne 0) {
        throw 'Word automation did not create a separate empty instance.'
    }
    $wordPid = $newWord[0].Id
    $owned = $true
    if ($ProcessRecord) {
        $wordProcess = $newWord[0]
        @{pid=$wordPid; created=$wordProcess.StartTime.ToFileTimeUtc().ToString()} |
            ConvertTo-Json -Compress | Set-Content -LiteralPath $ProcessRecord -Encoding UTF8
    }
    $wordApp.Visible = $false
    $wordApp.DisplayAlerts = 0
    $oldSecurity = $wordApp.AutomationSecurity
    $oldLinks = $wordApp.Options.UpdateLinksAtOpen
    $oldFields = $wordApp.Options.UpdateFieldsAtPrint
    $oldPrintLinks = $wordApp.Options.UpdateLinksAtPrint
    $wordApp.AutomationSecurity = 3
    $wordApp.Options.UpdateLinksAtOpen = $false
    $wordApp.Options.UpdateFieldsAtPrint = $false
    $wordApp.Options.UpdateLinksAtPrint = $false
    # Read only; no MRU entry or repair. The owned application remains hidden.
    $documents = $wordApp.Documents
    $openArgs = @($InputPath, $false, $true, $false)
    $wordDoc = $documents.GetType().InvokeMember('Open',
        [Reflection.BindingFlags]::InvokeMethod, $null, $documents, $openArgs)
    $wordDoc.Repaginate()
    # Native Word print layout, document content without revision markup.
    $wordDoc.ExportAsFixedFormat($OutputPath, 17, $false, 0, 0, 1, 1, 0, $false)
} finally {
    try {
        if ($null -ne $wordDoc) {
            try { $wordDoc.Close([ref]0) }
            finally { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($wordDoc) }
        }
    } finally {
        if ($null -ne $wordApp) {
            try {
                if ($owned) {
                    try {
                        if ($null -ne $oldSecurity) { $wordApp.AutomationSecurity = $oldSecurity }
                        if ($null -ne $oldLinks) { $wordApp.Options.UpdateLinksAtOpen = $oldLinks }
                        if ($null -ne $oldFields) { $wordApp.Options.UpdateFieldsAtPrint = $oldFields }
                        if ($null -ne $oldPrintLinks) { $wordApp.Options.UpdateLinksAtPrint = $oldPrintLinks }
                    } finally { $wordApp.Quit([ref]0) }
                }
            } finally { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($wordApp) }
        }
    }
}
