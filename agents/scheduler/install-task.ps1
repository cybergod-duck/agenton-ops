# install-task.ps1 — Register all AgentOn + Multi-Earn scheduled tasks
# Run once as Administrator:  powershell -ExecutionPolicy Bypass -File install-task.ps1

$PythonExe   = "python"
$AgentOnDir  = "C:\BC RESEARCH\AI_FACTORY\AgentOn"
$ScriptsDir  = "$AgentOnDir\scripts"
$MultiEarnDir = "$AgentOnDir\agents\multi-earn"

# ── Helper ────────────────────────────────────────────────────────────────────
function Register-Task {
    param(
        [string]$TaskName,
        [string]$Argument,
        [string]$WorkDir,
        [string]$TriggerType,       # "Repetition" | "Daily" | "AtStartup"
        [int]$IntervalMinutes = 0,
        [string]$StartTime = "08:00"
    )

    $Action = New-ScheduledTaskAction -Execute $PythonExe `
                                      -Argument $Argument `
                                      -WorkingDirectory $WorkDir

    if ($TriggerType -eq "Repetition") {
        $Trigger = New-ScheduledTaskTrigger -Once `
                        -At "$StartTime" `
                        -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
                        -RepetitionDuration (New-TimeSpan -Days 9999)
    } elseif ($TriggerType -eq "AtStartup") {
        $Trigger = New-ScheduledTaskTrigger -AtStartup
    } else {
        $Trigger = New-ScheduledTaskTrigger -Daily -At $StartTime
    }

    $Settings = New-ScheduledTaskSettingsSet `
                    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
                    -MultipleInstances IgnoreNew `
                    -StartWhenAvailable

    try {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Register-ScheduledTask -TaskName $TaskName `
                               -Action $Action `
                               -Trigger $Trigger `
                               -Settings $Settings `
                               -RunLevel Highest `
                               -Force | Out-Null
        Write-Host "[OK] Registered: $TaskName"
    } catch {
        Write-Host "[ERR] Failed to register $TaskName : $_"
    }
}

# ── 1. AgentOn Earn Loop — every 2 hours from 8AM ─────────────────────────────
Register-Task `
    -TaskName "AgentOn_EarnLoop" `
    -Argument "scripts\earn_loop.py" `
    -WorkDir $AgentOnDir `
    -TriggerType "Repetition" `
    -IntervalMinutes 120 `
    -StartTime "08:00"

# ── 2. BountyBook.ai Earn Loop — every 3 hours from 9AM ──────────────────────
Register-Task `
    -TaskName "MultiEarn_BountyBook" `
    -Argument "agents\multi-earn\bountybook_agent.py" `
    -WorkDir $AgentOnDir `
    -TriggerType "Repetition" `
    -IntervalMinutes 180 `
    -StartTime "09:00"

# ── 3. Claw Earn Loop — every 4 hours from 10AM ───────────────────────────────
Register-Task `
    -TaskName "MultiEarn_ClawEarn" `
    -Argument "agents\multi-earn\claw_earn_agent.py" `
    -WorkDir $AgentOnDir `
    -TriggerType "Repetition" `
    -IntervalMinutes 240 `
    -StartTime "10:00"

# ── 4. DealWork Loop — every 5 hours from 11AM ──────────────────────────────────
Register-Task `
    -TaskName "MultiEarn_DealWork" `
    -Argument "agents\multi-earn\dealwork_agent.py" `
    -WorkDir $AgentOnDir `
    -TriggerType "Repetition" `
    -IntervalMinutes 300 `
    -StartTime "11:00"

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Task Scheduler Status ==="
@("AgentOn_EarnLoop", "MultiEarn_BountyBook", "MultiEarn_ClawEarn", "MultiEarn_DealWork") | ForEach-Object {
    $t = Get-ScheduledTask -TaskName $_ -ErrorAction SilentlyContinue
    if ($t) {
        $next = ($t | Get-ScheduledTaskInfo).NextRunTime
        Write-Host "$_ -> State: $($t.State) | Next: $next"
    } else {
        Write-Host "$_ -> NOT FOUND"
    }
}

Write-Host ""
Write-Host "IMPORTANT: Fund the agent wallet with gas ETH on Base L2 (chain 8453):"
Write-Host "  Wallet: 0xAA37201F63183e816219e6D20c784Fef7C89901f"
Write-Host "  Send ~0.005 ETH on Base network (< $0.02 USD) for gas"
Write-Host "  Bridge: https://bridge.base.org"
