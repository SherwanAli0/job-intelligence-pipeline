# Run a job hunt now, from this machine.
#
#   .\run-now.ps1            full run: scores jobs and emails the digest
#   .\run-now.ps1 -DryRun    scrape and score, but no email and no state change
#
# Starts the same ECS Fargate task the schedule uses. The task runs in AWS, so
# you can close this window; -Watch just follows it until it finishes.
#
# Prefer not to use a terminal? GitHub -> Actions -> "Run job hunt now" ->
# Run workflow does the same thing from a browser.

param(
    [switch]$DryRun,
    [switch]$Watch
)

$ErrorActionPreference = "Stop"
$env:AWS_PAGER = ""

$cluster = "job-hunter"
$network = 'awsvpcConfiguration={subnets=["subnet-0d16bf613dcad401f","subnet-07ddd1e4c738c3e64"],securityGroups=["sg-03fa4900281aa7b73"],assignPublicIp=ENABLED}'

$overrides = "{}"
if ($DryRun) {
    $overrides = '{"containerOverrides":[{"name":"job-hunter","environment":[{"name":"DRY_RUN","value":"1"}]}]}'
    Write-Output "DRY RUN: no email will be sent and state will not be updated."
}

Write-Output "Starting hunt..."
$arn = aws ecs run-task `
    --cluster $cluster `
    --task-definition job-hunter `
    --launch-type FARGATE `
    --network-configuration $network `
    --overrides $overrides `
    --query 'tasks[0].taskArn' --output text

if (-not $arn) { Write-Output "Failed to start the task."; exit 1 }
$id = $arn.Split("/")[-1]

Write-Output ""
Write-Output "Started. Task id: $id"
Write-Output "Expect 15-45 minutes, depending on the Claude batch queue."
Write-Output "It runs in AWS, so closing this window is fine."
Write-Output ""
Write-Output "Logs: https://eu-central-1.console.aws.amazon.com/cloudwatch/home?region=eu-central-1#logsV2:log-groups/log-group/%252Fecs%252Fjob-hunter"

if (-not $Watch) {
    Write-Output ""
    Write-Output "Add -Watch to follow it here until it finishes."
    exit 0
}

Write-Output ""
Write-Output "Watching (Ctrl+C to stop watching; the hunt keeps running)..."
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 60
    $status = aws ecs describe-tasks --cluster $cluster --tasks $id --query 'tasks[0].lastStatus' --output text
    Write-Output ("  {0,3} min  {1}" -f ($i + 1), $status)
    if ($status -eq "STOPPED") { break }
}

$exit = aws ecs describe-tasks --cluster $cluster --tasks $id --query 'tasks[0].containers[0].exitCode' --output text
if ($exit -eq "0") {
    Write-Output "Finished successfully. Check your inbox."
} else {
    Write-Output "Finished with exit code $exit - check the CloudWatch logs above."
}
