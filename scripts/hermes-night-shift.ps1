param(
    [string]$Workspace = (Resolve-Path ".").Path,
    [string]$Day = "",
    [switch]$DryRun,
    [string]$Python = "python",
    [string]$Codex = "codex"
)

$ErrorActionPreference = "Stop"

function Resolve-Day {
    param([string]$RawDay)
    if ($RawDay -and $RawDay.Trim().Length -gt 0) {
        return $RawDay.Trim()
    }
    return (Get-Date).AddDays(-1).ToString("yyyy-MM-dd")
}

$Workspace = (Resolve-Path $Workspace).Path
$NightShiftDay = Resolve-Day -RawDay $Day
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$TempDir = Join-Path ([System.IO.Path]::GetTempPath()) "hermes-night-shift"
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null

$PacketPath = Join-Path $TempDir "briefing-packet.json"
$PlanPath = Join-Path $TempDir "today-plan.json"
$PromptPath = Join-Path $TempDir "planner-prompt.md"

Push-Location $RepoRoot
try {
    & $Python -m api.night_shift packet --workspace $Workspace --day $NightShiftDay | Set-Content -Path $PacketPath -Encoding UTF8
    $PacketJson = Get-Content -Path $PacketPath -Raw -Encoding UTF8

    $Prompt = @"
You are Hermes Night Shift, running offline at about 05:00.

Use the briefing packet below to close yesterday and prepare today. Act as:
- Planner / Orchestratore: write a concrete day plan with time-boxed routine blocks, priorities, project tasks, blockers, and next actions.
- Compactor / Memory Librarian: preserve only durable operational knowledge as concise notes in the JSON "notes" field. Do not include secrets.

Return ONLY valid JSON. No Markdown fences. Schema:
{
  "date": "YYYY-MM-DD",
  "priorities": ["..."],
  "routine": [{"time": "05:00-07:00", "title": "in queste 2 ore: X, Y", "tasks": ["..."]}],
  "projects": [{"project": "...", "next_action": "...", "blocks": ["..."], "tasks": ["..."]}],
  "notes": ["durable operational memory, concise"]
}

Briefing packet:
$PacketJson
"@
    Set-Content -Path $PromptPath -Value $Prompt -Encoding UTF8

    $ScheduleCommand = "schtasks /Create /SC DAILY /TN `"Hermes Night Shift`" /TR `"powershell.exe -ExecutionPolicy Bypass -File `"`"$($PSCommandPath)`"`" -Workspace `"`"$Workspace`"`"`" /ST 05:00"

    if ($DryRun) {
        Write-Output "Hermes Night Shift dry-run"
        Write-Output "Workspace: $Workspace"
        Write-Output "Day: $NightShiftDay"
        Write-Output "Briefing packet: $PacketPath"
        Write-Output "Planner prompt: $PromptPath"
        Write-Output ""
        Write-Output "Task Scheduler command (not registered):"
        Write-Output $ScheduleCommand
        Write-Output ""
        Write-Output "Packet preview:"
        Write-Output $PacketJson
        return
    }

    Get-Content -Path $PromptPath -Raw -Encoding UTF8 |
        & $Codex exec -C $Workspace --dangerously-bypass-approvals-and-sandbox -o $PlanPath

    if (-not (Test-Path $PlanPath)) {
        throw "Codex did not write a plan to $PlanPath"
    }

    & $Python -m api.night_shift apply-plan --workspace $Workspace --day $NightShiftDay --plan $PlanPath
    Write-Output ""
    Write-Output "Task Scheduler command (not registered):"
    Write-Output $ScheduleCommand
}
finally {
    Pop-Location
}
