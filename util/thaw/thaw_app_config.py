# thaw_app_config.py
#
# Copyright (C) 2011-2021 Vas Vasiliadis
# University of Chicago
#
# Set app configuration options for thaw utility
#
##
__author__ = 'Vas Vasiliadis <vas@uchicago.edu>'

import os

basedir = os.path.abspath(os.path.dirname(__file__))

class Config(object):

  CSRF_ENABLED = True

  AWS_REGION_NAME = "us-east-1"

  # AWS DynamoDB table
  AWS_DYNAMODB_ANNOTATIONS_TABLE = "haoyiran_annotations"

  # AWS SQS
  AWS_SQS_DID_UPGRADE_QUEUE_NAME = "haoyiran_a17_did_upgrade"
  AWS_SQS_WAIT_TIME = 20
  AWS_SQS_MAX_MESSAGES = 10

  # AWS SNS topic
  AWS_SNS_ARCHIVE_RETRIEVED_TOPIC = \
  "arn:aws:sns:us-east-1:127134666975:haoyiran_a17_archive_retrieved"

  # AWS Glacier vault
  AWS_GLACIER_VAULT_NAME = "ucmpcs"

### EOF
