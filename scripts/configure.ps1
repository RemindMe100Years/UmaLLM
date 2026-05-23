$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$ROOT_DIR = Split-Path -Parent $SCRIPT_DIR
$SETTINGS_FILE = Join-Path $SCRIPT_DIR "settings.json"
$CHARACTER_FILE = Join-Path $ROOT_DIR "data\character_memory.json"

$settings = $null

function Reload-Settings {
    $script:settings = Get-Content $SETTINGS_FILE -Raw -Encoding utf8 | ConvertFrom-Json
}

Reload-Settings

function Write-Menu {
    Reload-Settings
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  Translation API Server - Configuration" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  [1]  Model Name           : $($settings.model_name)"
    Write-Host "  [2]  API Server           : $($settings.api_server)"
    Write-Host "  [3]  API Key              : $(if ($settings.api_key) { $settings.api_key } else { '(not set)' })"
    Write-Host "  [4]  Context Lines        : $($settings.context_lines)"
    Write-Host "  [5]  Parallel Workers     : $($settings.parallel_workers)"
    Write-Host "  [6]  Chunk Size           : $($settings.chunk_size)"
    Write-Host "  [7]  Max Retries          : $($settings.max_retries)"
    Write-Host "  [8]  Temperature          : $($settings.temperature)"
    Write-Host "  [9]  Top P                : $($settings.top_p)"
    Write-Host "  [10] Top K                : $($settings.top_k)"
    Write-Host "  [11] Repetition Penalty   : $($settings.repetition_penalty)"
    Write-Host "  [12] Max Tokens           : $($settings.max_tokens)"
    Write-Host "  [13] Min P                : $($settings.min_p)"
    Write-Host "  [14] Frequency Penalty    : $($settings.frequency_penalty)"
    Write-Host "  [15] Presence Penalty     : $($settings.presence_penalty)"
    Write-Host "  [16] Output Language      : $($settings.output_language)"
    Write-Host "  [17] Trainer Gender       : $(Get-TrainerGender)"
    Write-Host "  [18] Strip Newlines       : $($settings.strip_newlines)"
    Write-Host "  [19] Append All Characters: $($settings.append_all_characters)"
    Write-Host "  [20] Jamdict Sanity Check  : $($settings.jamdict_sanity_check)"
    Write-Host ""
    Write-Host "  [21] Manage Characters" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  [q]  Exit" -ForegroundColor Green
    Write-Host ""
}

function Get-TrainerGender {
    $gender = python (Join-Path $SCRIPT_DIR "save_json.py") $CHARACTER_FILE --get "trainer.gender" 2>$null
    if ($gender) { return $gender }
    return "unknown"
}

function Save-Settings {
    $tmp = Join-Path $env:TEMP "settings_tmp.json"
    $json = $settings | ConvertTo-Json -Depth 10
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($tmp, $json, $utf8)
    python (Join-Path $SCRIPT_DIR "save_json.py") $SETTINGS_FILE -from $tmp
    Remove-Item $tmp -ErrorAction SilentlyContinue
    Write-Host ""
    Write-Host "Settings saved!" -ForegroundColor Green
}

function Update-TrainerGender($gender) {
    python (Join-Path $SCRIPT_DIR "save_json.py") $CHARACTER_FILE "" "trainer.gender=$gender"
}

function Manage-Characters {
    $charExit = $false
    while (-not $charExit) {
        Write-Host ""
        Write-Host "========================================" -ForegroundColor Magenta
        Write-Host "  Character Memory Manager" -ForegroundColor Magenta
        Write-Host "========================================" -ForegroundColor Magenta
        Write-Host ""
  Write-Host "  [1]  List All Entries"
    Write-Host "  [2]  Edit Entry"
    Write-Host "  [3]  Add Entry"
    Write-Host "  [4]  Delete Entry"
        Write-Host ""
        Write-Host "  [b]  Back to Main Menu" -ForegroundColor Green
        Write-Host ""

        $choice = Read-Host "  Select option"
        switch ($choice) {
            "b" { $charExit = $true }
            "1" { List-Characters }
            "2" { Edit-Character }
            "3" { Add-Character }
            "4" { Delete-Character }
            default {
                if ($choice -and $choice.Trim()) {
                    Write-Host ""
                    Write-Host "  Invalid option." -ForegroundColor Red
                }
            }
        }
    }
}

function List-Characters {
    $json = python (Join-Path $SCRIPT_DIR "save_json.py") $CHARACTER_FILE --list 2>$null
    if (-not $json) { Write-Host "  Failed to load characters." -ForegroundColor Red; return }
    $chars = $json | ConvertFrom-Json
    Write-Host ""
    Write-Host "  Total: $($chars.Count) characters" -ForegroundColor Cyan
    Write-Host ""
    foreach ($c in $chars) {
        $nick = if ($c.nickname) { " | Nick: $($c.nickname)" } else { "" }
        $notes = if ($c.notes) { " | $($c.notes)" } else { "" }
        Write-Host "  [$($c.i)] $($c.name) ($($c.gender))$nick$notes"
    }
    Write-Host ""
}

function Edit-Character {
    List-Characters
    $idx = Read-Host "  Enter entry index to edit (or -1 to cancel)"
    if ($idx -eq "-1" -or -not $idx) { return }
    if ($idx -notmatch '^\d+$') { Write-Host "  Invalid index." -ForegroundColor Red; return }

    $json = python (Join-Path $SCRIPT_DIR "save_json.py") $CHARACTER_FILE --list 2>$null
    $chars = $json | ConvertFrom-Json
    $idx = [int]$idx
    if ($idx -ge $chars.Count) { Write-Host "  Index out of range." -ForegroundColor Red; return }
    $c = $chars[$idx]

    $done = $false
    while (-not $done) {
        Write-Host ""
        Write-Host "  Editing: $($c.name)" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "  1 - Name       (current: $($c.name))" -ForegroundColor Gray
        Write-Host "  2 - JP Name    (current: $($c.jp))" -ForegroundColor Gray
        Write-Host "  3 - Gender     (current: $($c.gender))" -ForegroundColor Gray
        $nickDisplay = if ($c.nickname -is [array]) { $c.nickname -join ", " } elseif ($c.nickname) { $c.nickname } else { "(none)" }
        $notesDisplay = if ($c.notes) { $c.notes } else { "(none)" }
        Write-Host "  4 - Nickname   (current: $nickDisplay)" -ForegroundColor Gray
        Write-Host "  5 - Notes      (current: $notesDisplay)" -ForegroundColor Gray
        Write-Host "  0 - Done" -ForegroundColor Gray
        Write-Host ""

        $fieldChoice = Read-Host "  Select field to edit"
        switch ($fieldChoice.Trim()) {
            "0" { $done = $true }
            "1" {
                $newName = Read-Host "    New name"
                if ($newName -and $newName.Trim()) {
                    $tmp = Join-Path $env:TEMP "char_fields.json"
                    $utf8 = New-Object System.Text.UTF8Encoding $false
                    [System.IO.File]::WriteAllText($tmp, (@{ name = $newName.Trim() } | ConvertTo-Json -Depth 5), $utf8)
                    python (Join-Path $SCRIPT_DIR "save_json.py") $CHARACTER_FILE --edit $idx --fields $tmp
                    Remove-Item $tmp -ErrorAction SilentlyContinue
                    $c.name = $newName.Trim()
                    Write-Host "  Updated!" -ForegroundColor Green
                }
            }
            "2" {
                $newJp = Read-Host "    New JP name"
                if ($newJp -and $newJp.Trim()) {
                    $newJp = $newJp.Trim()
                    $utf8 = New-Object System.Text.UTF8Encoding $false
                    $notesTmp = $null
                    if ($c.notes) {
                        $notesTmp = Join-Path $env:TEMP "char_notes.txt"
                        [System.IO.File]::WriteAllText($notesTmp, $c.notes, $utf8)
                    }
                    python (Join-Path $SCRIPT_DIR "save_json.py") $CHARACTER_FILE --delete $idx
                    $cmdArgs = @("--add", $newJp, $c.name, $c.gender)
                    if ($c.nickname -is [array]) { $cmdArgs += @("--nickname", ($c.nickname -join ', ')) }
                    elseif ($c.nickname) { $cmdArgs += @("--nickname", $c.nickname) }
                    if ($notesTmp) { $cmdArgs += @("--notes-file", $notesTmp) }
                    python (Join-Path $SCRIPT_DIR "save_json.py") $CHARACTER_FILE $cmdArgs
                    if ($notesTmp) { Remove-Item $notesTmp -ErrorAction SilentlyContinue }
                    $c.jp = $newJp
                    $newList = python (Join-Path $SCRIPT_DIR "save_json.py") $CHARACTER_FILE --list 2>$null | ConvertFrom-Json
                    $idx = [int]($newList | Where-Object { $_.jp -eq $newJp }).i
                    Write-Host "  Updated!" -ForegroundColor Green
                }
            }
            "3" {
                if ($c.gender -eq "Not Applicable") {
                    Write-Host "  Cannot edit gender for Race/Other entries." -ForegroundColor Yellow
                } else {
                    $genderIn = Read-Host "    Gender (M/F)"
                    $genderUpper = $genderIn.Trim().ToUpper()
                    if ($genderUpper -eq "M" -or $genderUpper -eq "F") {
                        $gender = if ($genderUpper -eq "M") { "male" } else { "female" }
                        $tmp = Join-Path $env:TEMP "char_fields.json"
                        $utf8 = New-Object System.Text.UTF8Encoding $false
                        [System.IO.File]::WriteAllText($tmp, (@{ gender = $gender } | ConvertTo-Json -Depth 5), $utf8)
                        python (Join-Path $SCRIPT_DIR "save_json.py") $CHARACTER_FILE --edit $idx --fields $tmp
                        Remove-Item $tmp -ErrorAction SilentlyContinue
                        $c.gender = $gender
                        Write-Host "  Updated!" -ForegroundColor Green
                    } else {
                        Write-Host "  Invalid input." -ForegroundColor Yellow
                    }
                }
            }
            "4" {
                $nickIn = Read-Host "    Nickname (comma-separated for multiple, blank to clear)"
                if ($nickIn -and $nickIn.Trim()) {
                    $nick = if ($nickIn.Trim() -match ",") { @($nickIn.Trim() -split "," | ForEach-Object { $_.Trim() }) } else { $nickIn.Trim() }
                } else { $nick = $null }
                $tmp = Join-Path $env:TEMP "char_fields.json"
                $utf8 = New-Object System.Text.UTF8Encoding $false
                [System.IO.File]::WriteAllText($tmp, (@{ nickname = $nick } | ConvertTo-Json -Depth 5), $utf8)
                python (Join-Path $SCRIPT_DIR "save_json.py") $CHARACTER_FILE --edit $idx --fields $tmp
                Remove-Item $tmp -ErrorAction SilentlyContinue
                $c.nickname = $nick
                Write-Host "  Updated!" -ForegroundColor Green
            }
            "5" {
                $notesIn = Read-Host "    Notes (blank to clear)"
                if ($notesIn -and $notesIn.Trim()) {
                    $notes = $notesIn.Trim()
                } else { $notes = $null }
                $tmp = Join-Path $env:TEMP "char_fields.json"
                $utf8 = New-Object System.Text.UTF8Encoding $false
                [System.IO.File]::WriteAllText($tmp, (@{ notes = $notes } | ConvertTo-Json -Depth 5), $utf8)
                python (Join-Path $SCRIPT_DIR "save_json.py") $CHARACTER_FILE --edit $idx --fields $tmp
                Remove-Item $tmp -ErrorAction SilentlyContinue
                $c.notes = $notes
                Write-Host "  Updated!" -ForegroundColor Green
            }
            default { Write-Host "  Invalid option." -ForegroundColor Yellow }
        }
        if (-not $done) { Start-Sleep -Milliseconds 500 }
    }
    Write-Host ""
    Write-Host "  Done editing." -ForegroundColor Green
}

function Add-Character {
    Write-Host ""
    Write-Host "  Add New Entry" -ForegroundColor Cyan
    Write-Host ""

    $jp = Read-Host "    JP Name (required)"
    if (-not $jp -or -not $jp.Trim()) { Write-Host "  Cancelled." -ForegroundColor Yellow; return }
    $jp = $jp.Trim()

    $name = Read-Host "    Name (required)"
    if (-not $name -or -not $name.Trim()) { Write-Host "  Cancelled." -ForegroundColor Yellow; return }
    $name = $name.Trim()

    $typeIn = Read-Host "    Type (C=Character, R=Race/Other)"
    $typeUpper = $typeIn.Trim().ToUpper()
    if ($typeUpper -eq "R") {
        $gender = "Not Applicable"
        $nick = $null
        $notes = $null
    } else {
        $genderIn = Read-Host "    Gender (M/F)"
        $genderUpper2 = $genderIn.Trim().ToUpper()
        if ($genderUpper2 -eq "M") { $gender = "male" }
        elseif ($genderUpper2 -eq "F") { $gender = "female" }
        else { Write-Host "  Invalid gender." -ForegroundColor Red; return }

        $nickIn = Read-Host "    Nickname (optional, comma-separated for multiple)"
        $nick = if ($nickIn -and $nickIn.Trim()) { $nickIn.Trim() } else { $null }

        $notesIn = Read-Host "    Notes (optional)"
        $notes = if ($notesIn -and $notesIn.Trim()) { $notesIn.Trim() } else { $null }
    }

    $cmdArgs = @("--add", $jp, $name, $gender)
    if ($nick) {
        $cmdArgs += @("--nickname", $nick)
    }
    if ($notes) {
        $tmp = Join-Path $env:TEMP "char_notes.txt"
        $utf8 = New-Object System.Text.UTF8Encoding $false
        [System.IO.File]::WriteAllText($tmp, $notes, $utf8)
        $cmdArgs += @("--notes-file", $tmp)
    }

    python (Join-Path $SCRIPT_DIR "save_json.py") $CHARACTER_FILE $cmdArgs
    if ($notes) { Remove-Item $tmp -ErrorAction SilentlyContinue }
    Write-Host ""
    Write-Host "  Entry added!" -ForegroundColor Green
}

function Delete-Character {
    List-Characters
    $idx = Read-Host "  Enter entry index to delete (or -1 to cancel)"
    if ($idx -eq "-1" -or -not $idx) { return }
    if ($idx -notmatch '^\d+$') { Write-Host "  Invalid index." -ForegroundColor Red; return }

    $json = python (Join-Path $SCRIPT_DIR "save_json.py") $CHARACTER_FILE --list 2>$null
    $chars = $json | ConvertFrom-Json
    $idx = [int]$idx
    if ($idx -ge $chars.Count) { Write-Host "  Index out of range." -ForegroundColor Red; return }
    $c = $chars[$idx]

    $confirm = Read-Host "  Are you sure you want to delete $($c.name) from memory? (y/n)"
    if ($confirm.Trim().ToLower() -eq "y") {
        python (Join-Path $SCRIPT_DIR "save_json.py") $CHARACTER_FILE --delete $idx
        Write-Host ""
        Write-Host "  Character deleted." -ForegroundColor Green
    } else {
        Write-Host "  Cancelled." -ForegroundColor Yellow
    }
}

$exit = $false

while (-not $exit) {
    Write-Menu

    $choice = Read-Host "  Enter number"

    switch ($choice) {
        "q" { $exit = $true }
        "1" {
            $val = Read-Host "  Model name (current: $($settings.model_name))"
            if ($val -and $val.Trim()) { $settings.model_name = $val.Trim(); Save-Settings }
        }
        "2" {
            $val = Read-Host "  API server URL (current: $($settings.api_server))"
            if ($val -and $val.Trim()) { $settings.api_server = $val.Trim(); Save-Settings }
        }
        "3" {
            $current = if ($settings.api_key) { $settings.api_key } else { "(not set)" }
            $val = Read-Host "  API key (current: $current, 'null' to clear)"
            if ($val -and $val.Trim()) {
                if ($val.Trim() -eq "null") { $settings.api_key = $null }
                else { $settings.api_key = $val.Trim() }
                Save-Settings
            }
        }
        "4" {
            $val = Read-Host "  Context lines (current: $($settings.context_lines))"
            if ($val -and $val.Trim() -match '^\d+$') { $settings.context_lines = [int]$val.Trim(); Save-Settings }
        }
        "5" {
            $val = Read-Host "  Parallel workers (current: $($settings.parallel_workers))"
            if ($val -and $val.Trim() -match '^\d+$') { $settings.parallel_workers = [int]$val.Trim(); Save-Settings }
        }
        "6" {
            $val = Read-Host "  Chunk size (current: $($settings.chunk_size))"
            if ($val -and $val.Trim() -match '^\d+$') { $settings.chunk_size = [int]$val.Trim(); Save-Settings }
        }
        "7" {
            $val = Read-Host "  Max retries (current: $($settings.max_retries))"
            if ($val -and $val.Trim() -match '^\d+$') { $settings.max_retries = [int]$val.Trim(); Save-Settings }
        }
        "8" {
            $val = Read-Host "  Temperature (current: $($settings.temperature))"
            if ($val -and $val.Trim() -match '^\d+(\.\d+)?$') { $settings.temperature = [double]$val.Trim(); Save-Settings }
        }
        "9" {
            $val = Read-Host "  Top P (current: $($settings.top_p))"
            if ($val -and $val.Trim() -match '^\d+(\.\d+)?$') { $settings.top_p = [double]$val.Trim(); Save-Settings }
        }
        "10" {
            $val = Read-Host "  Top K (current: $($settings.top_k))"
            if ($val -and $val.Trim() -match '^\d+$') { $settings.top_k = [int]$val.Trim(); Save-Settings }
        }
        "11" {
            $val = Read-Host "  Repetition Penalty (current: $($settings.repetition_penalty))"
            if ($val -and $val.Trim() -match '^\d+(\.\d+)?$') { $settings.repetition_penalty = [double]$val.Trim(); Save-Settings }
        }
        "12" {
            $val = Read-Host "  Max Tokens (current: $($settings.max_tokens))"
            if ($val -and $val.Trim() -match '^\d+$') { $settings.max_tokens = [int]$val.Trim(); Save-Settings }
        }
        "13" {
            $val = Read-Host "  Min P (current: $($settings.min_p))"
            if ($val -and $val.Trim() -match '^\d+(\.\d+)?$') { $settings.min_p = [double]$val.Trim(); Save-Settings }
        }
        "14" {
            $val = Read-Host "  Frequency Penalty (current: $($settings.frequency_penalty))"
            if ($val -and $val.Trim() -match '^\d+(\.\d+)?$') { $settings.frequency_penalty = [double]$val.Trim(); Save-Settings }
        }
        "15" {
            $val = Read-Host "  Presence Penalty (current: $($settings.presence_penalty))"
            if ($val -and $val.Trim() -match '^\d+(\.\d+)?$') { $settings.presence_penalty = [double]$val.Trim(); Save-Settings }
        }
        "16" {
            $val = Read-Host "  Output language (current: $($settings.output_language))"
            if ($val -and $val.Trim()) {
                if ($settings.supported_languages_list.ContainsKey($val.Trim())) {
                    $settings.output_language = $val.Trim(); Save-Settings
                } else {
                    Write-Host "  Invalid language!" -ForegroundColor Red
                }
            }
        }
       "17" {
            $val = Read-Host "  Trainer gender (current: $(Get-TrainerGender), M/F)"
            $valUpper = $val.Trim().ToUpper()
            if ($valUpper -eq "M") { Update-TrainerGender "male"; Write-Host "  Saved." -ForegroundColor Green }
            elseif ($valUpper -eq "F") { Update-TrainerGender "female"; Write-Host "  Saved." -ForegroundColor Green }
            elseif ($val -and $val.Trim()) { Write-Host "  Invalid input! Use M or F." -ForegroundColor Red }
        }
        "18" {
            $val = Read-Host "  Strip newlines (current: $($settings.strip_newlines), y/n)"
            $valLower = $val.Trim().ToLower()
            if ($valLower -eq "y") { $settings.strip_newlines = $true; Save-Settings }
            elseif ($valLower -eq "n") { $settings.strip_newlines = $false; Save-Settings }
            elseif ($val -and $val.Trim()) { Write-Host "  Invalid input! Use y or n." -ForegroundColor Red }
        }
       "19" {
            $val = Read-Host "  Append all characters (current: $($settings.append_all_characters), y/n)"
            $valLower = $val.Trim().ToLower()
            if ($valLower -eq "y") { $settings.append_all_characters = $true; Save-Settings }
            elseif ($valLower -eq "n") { $settings.append_all_characters = $false; Save-Settings }
            elseif ($val -and $val.Trim()) { Write-Host "  Invalid input! Use y or n." -ForegroundColor Red }
        }
        "20" {
            $val = Read-Host "  Jamdict sanity check (current: $($settings.jamdict_sanity_check), y/n)"
            $valLower = $val.Trim().ToLower()
            if ($valLower -eq "y") { $settings.jamdict_sanity_check = $true; Save-Settings }
            elseif ($valLower -eq "n") { $settings.jamdict_sanity_check = $false; Save-Settings }
            elseif ($val -and $val.Trim()) { Write-Host "  Invalid input! Use y or n." -ForegroundColor Red }
        }
        "21" { Manage-Characters }
        default {
            if ($choice -and $choice.Trim()) {
                Write-Host ""
                Write-Host "  Invalid option." -ForegroundColor Red
            }
        }
    }
}
