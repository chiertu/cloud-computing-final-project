# archive_app_config.py
#
# Copyright (C) 2011-2022 Vas Vasiliadis
# University of Chicago
#
# Set app configuration options for archive utility
#
##
__author__ = 'Vas Vasiliadis <vas@uchicago.edu>'

class Config(object):

  CSRF_ENABLED = True

  AWS_REGION_NAME = "us-east-1"

  # AWS SQS queues
  AWS_SQS_WAIT_ENDED_QUEUE_NAME = "haoyiran_a17_wait_ended"
  AWS_SQS_WAIT_TIME = 20
  AWS_SQS_MAX_MESSAGES = 10

  # AWS DynamoDB table
  AWS_DYNAMODB_ANNOTATIONS_TABLE = "haoyiran_annotations"

  # AWS Glacier vault
  AWS_GLACIER_VAULT_NAME = "ucmpcs"

### EOF