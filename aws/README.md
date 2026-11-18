# AWS infrastructure

Note: the GitHub repository is `job-intelligence-pipeline` (renamed from `job-hunter`). GitHub issues OIDC tokens for repositories renamed after 15 July 2026 with an ID-based subject, so the trust policy must name `repo:SherwanAli0@169561155/job-intelligence-pipeline@1233781936:ref:refs/heads/main` (owner ID, repository ID and branch), not the name-based form. The AWS resources below keep their `job-hunter` names.

The pipeline runs as a scheduled **ECS Fargate task** in `eu-central-1`.

## Why Fargate rather than Lambda

A full scrape was measured at **40.3 minutes** (observed range 36-72) at the July 2026
migration; a later healthy run took 2 h 03 min. Lambda's
maximum execution time is 15 minutes and is not configurable, so a Lambda
monolith was impossible and a split would have required fanning the scrape
across parallel invocations. Fargate has no time limit and runs the same
container unchanged.

## Pieces

| Resource | Name | Purpose |
|---|---|---|
| ECR repo | `job-hunter` | container image |
| ECS cluster | `job-hunter` | Fargate capacity |
| Task definition | `job-hunter` | 0.5 vCPU / 2 GB |
| Task role | `jobHunterTaskRole` | app permissions: own S3 prefix + SSM path only |
| Execution role | `jobHunterExecutionRole` | ECS agent: pull image, write logs |
| Scheduler role | `jobHunterSchedulerRole` | may run only this task definition |
| Schedules | `job-hunter-morning` / `-afternoon` | 10:00 and 17:00 **Europe/Berlin**. The afternoon run was created for 16:00, was off from 2026-08-18 to 2026-11-17, and now passes `JOBHUNTER_SOURCE_WINDOW=since_last_run` as a container override, so it asks the boards only for what is new since the morning run (see the root README). Schedule definitions live in EventBridge, not in this repo: confirm with `aws scheduler get-schedule` |
| Schedules | `job-hunter-linkedin-0630` / `-1330` / `-2230` | 06:30, 13:30 and 22:30 **Europe/Berlin** (since 2026-11-18). Same target as the afternoon schedule, with `JOBHUNTER_MODE=linkedin` as a container override: the task reads LinkedIn on its own (`main.read_linkedin_only`), keeps what it found for the next email run (`linkedin_inbox.jsonl` in the state prefix) and sends nothing. It skips itself while an email run holds `run-full.claim`. An image without this mode would start a full run with an email, so these schedules were created only after the image that has it was deployed |
| Logs | `/ecs/job-hunter` | 14-day retention |

## Cost control

- Tasks run in **public subnets with `assignPublicIp=ENABLED`**. A private
  subnet would require a NAT Gateway at roughly $33/month, which would dwarf
  every other cost here. Do not move these tasks into private subnets.
- Log retention is set explicitly; CloudWatch logs never expire by default.
- Estimated at migration (July 2026): roughly $1.40/month of Fargate at two
  40-minute runs per day, plus ~$0.14 of ECR storage. These are back-of-envelope
  estimates, not billing data, and the Fargate part is about half that at one
  run per day. Cost Explorer has the actuals.

## Deploying a new image

Normally: push to `main`. [deploy.yml](../.github/workflows/deploy.yml) runs the tests,
builds the image, pushes it to ECR tagged with the commit SHA and `latest`, and registers
a new task definition revision that points at the SHA-tagged image. The schedules target
the task definition family, so the next run uses the newest revision. Pushes that only
change Markdown files, `cv/` or `data/` do not deploy.
`task-definition.json` in this folder is the original template (image `:latest`); the
live revisions are the ones deploy.yml registers.

A manual push of `:latest` (below) no longer changes what runs once a revision pins a
SHA tag, because the task definition, not the tag, decides the image. After a manual
push you would also have to register a revision that uses the new image.

The `aws/*.json` files are templates: they contain placeholders, not the owner's deployment identifiers. Create ignored `personal/aws-deployment.json` from `aws/deployment.example.json`, then render private copies before using them with AWS CLI:

```powershell
python aws/render_templates.py --config personal/aws-deployment.json --output-dir .aws-rendered
```

The rendered JSON preserves the existing template policies and network choices. Do not commit rendered files. The live deploy still copies the existing task definition and changes its image; it does not apply these initial-setup templates or alter IAM/network settings.

GitHub Actions uses private repository variables `AWS_DEPLOY_ROLE_ARN` and `JOBHUNTER_NETWORK_CONFIGURATION`. They select the existing deploy role and the same network JSON; they are not credentials. Scoring facts are separate: see [private profiles](private-profiles.md). Git history and old public workflow logs retain historical identifiers.

For a manual image build, obtain the registry from your private deployment settings:

```powershell
$deployment = Get-Content personal/aws-deployment.json -Raw | ConvertFrom-Json
$registry = "{0}.dkr.ecr.{1}.amazonaws.com" -f $deployment.AWS_ACCOUNT_ID, $deployment.AWS_REGION
docker build -t job-hunter:local .
aws ecr get-login-password --region $deployment.AWS_REGION | docker login --username AWS --password-stdin $registry
docker tag job-hunter:local "${registry}/job-hunter:latest"
docker push "${registry}/job-hunter:latest"
```

## Running one manually

```powershell
.\aws\run-now.ps1 -DeploymentConfig personal/aws-deployment.json
.\aws\run-now.ps1 -DryRun -Watch -DeploymentConfig personal/aws-deployment.json
```

A dry run still scrapes live sources and pays for scoring. It sends no email and writes no state. The normal full run sends the digest and updates state. The manual GitHub workflow uses the same private network variable.

## Gotcha worth remembering

`ssm:GetParametersByPath` is authorised against **the path itself**, not only
its children. A policy granting `parameter/job-hunter/*` alone returns
AccessDenied; both ARNs are required.
