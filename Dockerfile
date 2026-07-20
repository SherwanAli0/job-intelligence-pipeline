# Job Hunter container image.
#
# Built FROM the AWS Lambda Python base image for two reasons: it ships the
# Lambda Runtime Interface Client so the same image can be deployed as a
# Lambda function, and it is a Linux image, which matters because the
# dependencies (pandas, numpy, lxml) are compiled wheels — building this on a
# Windows host with plain `pip install -t` would produce Windows binaries that
# cannot execute on AWS at all.
#
# The image also runs on Fargate by overriding the entrypoint:
#   docker run --entrypoint python <image> handler.py
FROM public.ecr.aws/lambda/python:3.11

# Dependencies first: this layer is cached and only rebuilt when
# requirements.txt changes, so code edits rebuild in seconds rather than
# re-compiling pandas every time.
COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Application code. State and secrets are NOT baked in: state lives in S3 via
# storage.py and secrets come from SSM via secrets_loader.py, both driven by
# environment variables set on the function/task.
COPY *.py ${LAMBDA_TASK_ROOT}/
COPY golden/ ${LAMBDA_TASK_ROOT}/golden/

CMD [ "handler.lambda_handler" ]
