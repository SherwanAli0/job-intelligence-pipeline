# AWS infrastructure

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
| Schedules | `job-hunter-morning` / `-afternoon` | Created as 10:00 and 16:00 **Europe/Berlin**. The repo's own notes say the afternoon run was switched off on 2026-08-18 and the pipeline now runs once a day (see the root README). Schedule definitions live in EventBridge, not in this repo: confirm with `aws scheduler get-schedule` |
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
change Markdown files, `cv/`, `run_stats.jsonl` or `seen_jobs.json` do not deploy.
`task-definition.json` in this folder is the original template (image `:latest`); the
live revisions are the ones deploy.yml registers.

A manual push of `:latest` (below) no longer changes what runs once a revision pins a
SHA tag, because the task definition, not the tag, decides the image. After a manual
push you would also have to register a revision that uses the new image.

```bash
docker build -t job-hunter:local .
aws ecr get-login-password --region eu-central-1 \
  | docker login --username AWS --password-stdin 278371786270.dkr.ecr.eu-central-1.amazonaws.com
docker tag job-hunter:local 278371786270.dkr.ecr.eu-central-1.amazonaws.com/job-hunter:latest
docker push 278371786270.dkr.ecr.eu-central-1.amazonaws.com/job-hunter:latest
```

## Running one manually

```bash
aws ecs run-task --cluster job-hunter --task-definition job-hunter --launch-type FARGATE \
  --network-configuration 'awsvpcConfiguration={subnets=["subnet-0d16bf613dcad401f","subnet-07ddd1e4c738c3e64"],securityGroups=["sg-03fa4900281aa7b73"],assignPublicIp=ENABLED}'
```

Add `--overrides '{"containerOverrides":[{"name":"job-hunter","environment":[{"name":"DRY_RUN","value":"1"}]}]}'`
to run the pipeline without sending email or updating `seen_jobs.json`. It is not a
cheap check: it still scrapes live sources and pays for the Claude scoring call, and it
may still save newly drafted screening answers to the answer bank. It also skips the
overlap claim, so it can run alongside a scheduled run.

## Gotcha worth remembering

`ssm:GetParametersByPath` is authorised against **the path itself**, not only
its children. A policy granting `parameter/job-hunter/*` alone returns
AccessDenied; both ARNs are required.
