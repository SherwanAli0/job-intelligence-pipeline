# Run the same Fargate task as the schedule, using ignored private network settings.
# .\run-now.ps1 [-DryRun] [-Watch] [-DeploymentConfig personal\aws-deployment.json]
param(
    [switch]$DryRun,
    [switch]$Watch,
    [string]$DeploymentConfig = (Join-Path $PSScriptRoot 'personal\aws-deployment.json')
)

$ErrorActionPreference = 'Stop'
$env:AWS_PAGER = ''
if (-not (Test-Path -LiteralPath $DeploymentConfig)) {
    throw 'Create ignored personal/aws-deployment.json from aws/deployment.example.json first.'
}
$deployment = Get-Content -LiteralPath $DeploymentConfig -Raw | ConvertFrom-Json
foreach ($field in @('JOBHUNTER_SUBNET_1', 'JOBHUNTER_SUBNET_2', 'JOBHUNTER_SECURITY_GROUP', 'AWS_REGION')) {
    if (-not $deployment.$field -or $deployment.$field -match '[<>]') {
        throw "Private deployment configuration is missing a real value for $field"
    }
}
$cluster = 'job-hunter'
$region = $deployment.AWS_REGION
$network = @{
    awsvpcConfiguration = @{
        subnets = @($deployment.JOBHUNTER_SUBNET_1, $deployment.JOBHUNTER_SUBNET_2)
        securityGroups = @($deployment.JOBHUNTER_SECURITY_GROUP)
        assignPublicIp = 'ENABLED'
    }
} | ConvertTo-Json -Compress -Depth 4
$overrides = '{}'
if ($DryRun) {
    $overrides = '{"containerOverrides":[{"name":"job-hunter","environment":[{"name":"DRY_RUN","value":"1"}]}]}'
    Write-Output 'DRY RUN: no email or state writes; live scraping and paid scoring still run.'
}

$taskArn = aws ecs run-task --region $region --cluster $cluster --task-definition job-hunter `
    --launch-type FARGATE --network-configuration $network --overrides $overrides `
    --query 'tasks[0].taskArn' --output text
if ($LASTEXITCODE -ne 0 -or -not $taskArn -or $taskArn -eq 'None') {
    throw 'Failed to start the task.'
}
$taskId = $taskArn.Split('/')[-1]
Write-Output "Started task $taskId. It continues in AWS if this terminal closes."
if (-not $Watch) { exit 0 }

for ($poll = 0; $poll -lt 60; $poll++) {
    Start-Sleep -Seconds 60
    $taskStatus = aws ecs describe-tasks --region $region --cluster $cluster --tasks $taskId `
        --query 'tasks[0].lastStatus' --output text
    Write-Output "Task status: $taskStatus"
    if ($taskStatus -eq 'STOPPED') { break }
}
$taskExitCode = aws ecs describe-tasks --region $region --cluster $cluster --tasks $taskId `
    --query 'tasks[0].containers[0].exitCode' --output text
if ($taskExitCode -eq '0') { Write-Output 'Finished successfully.' }
elseif ($taskExitCode -eq 'None') { Write-Output 'The task is still running; inspect its CloudWatch logs.' }
else { throw "Task failed with exit code $taskExitCode; inspect its CloudWatch logs." }
