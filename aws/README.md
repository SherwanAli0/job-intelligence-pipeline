# AWS infrastructure

The pipeline runs as a scheduled **ECS Fargate task** in `eu-central-1`.

## Why Fargate rather than Lambda

A full scrape was measured at **40.3 minutes** (observed range 36-72). Lambda's
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
| Schedules | `job-hunter-morning` / `-afternoon` | 11:00 and 17:00 **Europe/Berlin** |
| Logs | `/ecs/job-hunter` | 14-day retention |

## Cost control

- Tasks run in **public subnets with `assignPublicIp=ENABLED`**. A private
  subnet would require a NAT Gateway at roughly $33/month, which would dwarf
  every other cost here. Do not move these tasks into private subnets.
- Log retention is set explicitly; CloudWatch logs never expire by default.
- Roughly $1.40/month of Fargate at two 40-minute runs per day, plus ~$0.14
  of ECR storage.

## Deploying a new image

```bash
docker build -t job-hunter:local .
aws ecr get-login-password --region eu-central-1 \
  | docker login --username AWS --password-stdin 278371786270.dkr.ecr.eu-central-1.amazonaws.com
docker tag job-hunter:local 278371786270.dkr.ecr.eu-central-1.amazonaws.com/job-hunter:latest
docker push 278371786270.dkr.ecr.eu-central-1.amazonaws.com/job-hunter:latest
```

Fargate pulls `:latest` on each run, so no redeploy step is needed.

## Running one manually

```bash
aws ecs run-task --cluster job-hunter --task-definition job-hunter --launch-type FARGATE \
  --network-configuration 'awsvpcConfiguration={subnets=["subnet-0d16bf613dcad401f","subnet-07ddd1e4c738c3e64"],securityGroups=["sg-03fa4900281aa7b73"],assignPublicIp=ENABLED}'
```

Add `--overrides '{"containerOverrides":[{"name":"job-hunter","environment":[{"name":"DRY_RUN","value":"1"}]}]}'`
to exercise the pipeline without sending email or writing state.

## Gotcha worth remembering

`ssm:GetParametersByPath` is authorised against **the path itself**, not only
its children. A policy granting `parameter/job-hunter/*` alone returns
AccessDenied; both ARNs are required.
