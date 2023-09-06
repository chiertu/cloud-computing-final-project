# ann_config.py
#
# Copyright (C) 2011-2022 Vas Vasiliadis
# University of Chicago
#
# Set GAS annotator configuration options
#
##
__author__ = 'Vas Vasiliadis <vas@uchicago.edu>'

class Config(object):

  CSRF_ENABLED = True

  ANNOTATOR_BASE_DIR = "/home/ubuntu/gas/ann/"
  ANNOTATOR_JOBS_DIR = "/home/ubuntu/gas/ann/jobs"
  ANNOTATOR_RUN_SCRIPT_PATH = "/home/ubuntu/gas/ann/run.py"

  AWS_REGION_NAME = "us-east-1"

  # AWS S3 upload parameters
  AWS_S3_INPUTS_BUCKET = "gas-inputs"
  AWS_S3_RESULTS_BUCKET = "gas-results"

  # AWS SNS topics

  # AWS SQS queues
  AWS_SQS_WAIT_TIME = 20
  AWS_SQS_MAX_MESSAGES = 10
  AWS_SQS_NAME = "haoyiran_a17_job_requests"

  # AWS DynamoDB
  AWS_DYNAMODB_ANNOTATIONS_TABLE = "haoyiran_annotations"

  # AWS Lambda
  
  # AWS Step Function
  AWS_STEPFUNCTION_WAITING_ENGINE = "arn:aws:states:us-east-1:127134666975:stateMachine:haoyiran_a17_waiting_engine"
### EOF