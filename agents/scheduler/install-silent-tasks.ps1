# install-silent-tasks.ps1 — Register silent versions of AgentOn + Multi-Earn tasks at user level
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$VbsPath = "C:\BCRESE~1\AI_FAC~1\scripts\run_hidden_args.vbs"
$AgentOnDir = "C:\BCRESE~1\AI_FAC~1\AgentOn"

# Helper to register a task using schtasks.exe to bypass admin privilege requirements
function Register-UserTask {
    param(
        [string]$TaskName,
        [string]$ScriptPath,
        [int]$IntervalMinutes,
        [string]$StartTime
    )

    $TaskNameSilent = "${TaskName}_Silent"
    $TaskRun = "wscript.exe $VbsPath python $AgentOnDir\$ScriptPath"

    Write-Host "Registering $TaskNameSilent..."
    & schtasks.exe /create /tn $TaskNameSilent /tr $TaskRun /sc daily /st $StartTime /ri $IntervalMinutes /du 24:00 /f
}

# 1. AgentOn Earn Loop — every 2 hours from 8AM
Register-UserTask -TaskName "AgentOn_EarnLoop" -ScriptPath "scripts\earn_loop.py" -IntervalMinutes 120 -StartTime "08:00"

# 2. AgentOn Quest Runner — every 2 hours from 8:30AM
Register-UserTask -TaskName "AgentOn_QuestRunner" -ScriptPath "agents\agenton\quest_runner.py" -IntervalMinutes 120 -StartTime "08:30"

# 3. BountyBook.ai Earn Loop — every 3 hours from 9AM
Register-UserTask -TaskName "MultiEarn_BountyBook" -ScriptPath "agents\multi-earn\bountybook_agent.py" -IntervalMinutes 180 -StartTime "09:00"

# 4. Claw Earn Loop — every 4 hours from 10AM
Register-UserTask -TaskName "MultiEarn_ClawEarn" -ScriptPath "agents\multi-earn\claw_earn_agent.py" -IntervalMinutes 240 -StartTime "10:00"

# 5. DealWork Loop — every 5 hours from 11AM
Register-UserTask -TaskName "MultiEarn_DealWork" -ScriptPath "agents\multi-earn\dealwork_agent.py" -IntervalMinutes 300 -StartTime "11:00"

# 6. ugig.net Loop — every 5 hours from 11:30AM
Register-UserTask -TaskName "MultiEarn_Ugig" -ScriptPath "agents\multi-earn\ugig_agent.py" -IntervalMinutes 300 -StartTime "11:30"

Write-Host "`nAll silent tasks registered successfully!"
