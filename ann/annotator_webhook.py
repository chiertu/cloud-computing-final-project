# annotator_webhook.py
#
# NOTE: This file lives on the AnnTools instance
# Modified to run as a web server that can be called by SNS to process jobs
# Run using: python annotator_webhook.py
#
# Copyright (C) 2011-2022 Vas Vasiliadis
# University of Chicago
##
__author__ = 'Vas Vasiliadis <vas@uchicago.edu>'

import requests
from flask import Flask, jsonify, request
import boto3
import botocore
import json
import os
import subprocess

app = Flask(__name__)
environment = 'ann_config.Config'
app.config.from_object(environment)

'''
A13 - Replace polling with webhook in annotator

Receives request from SNS; queries job queue and processes message.
Reads request messages from SQS and runs AnnTools as a subprocess.
Updates the annotations database with the status of the request.
'''
@app.route('/process-job-request', methods=['GET', 'POST'])
def annotate():

  # get SQS queue, exit if cannot get queue
  sqs_resource = boto3.resource("sqs", region_name = app.config['AWS_REGION_NAME'])
  try:
    queue = sqs_resource.get_queue_by_name(QueueName = app.config['AWS_SQS_NAME'])
  except botocore.exceptions.ClientError as e:
    return jsonify({
      "code": 503,
      "message": f'Unable to get SQS queue: {e}'
      }), 503

  # if method is GET, exit
  if (request.method == 'GET'):
    return jsonify({
      "code": 405, 
      "message": f'Expecting SNS POST request: {e}'
    }), 405
  
  # if method is POST, check header
  elif (request.method == 'POST'):
    try:
      js = json.loads(request.data)
    except json.JSONDecodeError as e:
      return jsonify({
        "code": 500,
        "message": f'Unable to decode json data: {e}'
      }), 500
    
    # Check header for message type
    hdr = request.headers.get('x-amz-sns-message-type')
    # Confirm SNS topic subscription confirmation
    # referring to: https://gist.github.com/iMilnb/bf27da3f38272a76c801
    if hdr == 'SubscriptionConfirmation' and 'SubscribeURL' in js:
      r = requests.get(js['SubscribeURL'])
      print(r)
      if r.status_code == 200:
        return jsonify({
          "code": 200,
          "message": f'You have succefully subscribed to SNS'
          }), 200
      else:
        return jsonify({
          "code": 500,
          "message": f'Unable to subscribe to SNS'
        }), 500
      
    # Poll SQS for tasks
    elif hdr == 'Notification':
      wait_time = int(app.config['AWS_SQS_WAIT_TIME'])
      max_num_message = int(app.config['AWS_SQS_MAX_MESSAGES'])
      try:
        messages = queue.receive_messages(WaitTimeSeconds = wait_time, 
                                          MaxNumberOfMessages = max_num_message)
      except botocore.exceptions.ClientError as e:
        return jsonify({
          "code": 500,
          "message": f'Unable to receive message from SQS: {e}'
        }), 500

      # Start processing messages from SQS 
      if len(messages) > 0:
        print(f'Received {str(len(messages))} messages...')

        # Iterate each message
        for message in messages:
          # Parse JSON message
          print("new message")
          try:
            msg_body = json.loads(json.loads(message.body)["Message"])
            print(msg_body)
          except ValueError as e:
            return jsonify({
              "code": 500,
              "message": f'Unable to decode message into json: {e}'
            }), 500
          
          try: 
            bucket = msg_body["s3_inputs_bucket"]
            inputfile_path_s3 = msg_body["s3_key_input_file"]
            file_name = msg_body["input_file_name"]
            job_id = msg_body["job_id"]
            user_id = msg_body["user_id"]
          except KeyError as e:
            return jsonify({
                "code": 500,
                "message": f'Missing field in message: {e}'
                }), 500

          # variables for file paths
          base_directory = app.config['ANNOTATOR_BASE_DIR']
          user_directory = base_directory + "jobs/" + user_id
          job_directory = base_directory + "jobs/" + user_id + "/" + job_id
          inputfile_path = job_directory + "/" + file_name

          # create user and job folder
          try: 
            user_does_exist = os.path.exists(user_directory)
            if not user_does_exist:
                os.makedirs(user_directory)
            job_does_exist = os.path.exists(job_directory)
            if not job_does_exist:
                os.makedirs(job_directory)
          except OSError as e:
            return jsonify({
              "code": 500,
              "message": f'Unable to create job folder on instance: {e}'
            }), 500

          # download file from s3
          # referring to documentation: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html#S3.Client.download_file
          s3_resource = boto3.resource('s3', region_name = app.config['AWS_REGION_NAME'])
          try:
            print("Downloading input file from s3...")
            s3_resource.meta.client.download_file(bucket, inputfile_path_s3, inputfile_path)
          except botocore.exceptions.ClientError as e:
            if e.response['Error']['Code'] == "404": 
                return jsonify({
                  "code": 404,
                  "message": f'File does not exit on S3: {e}'
                  }), 404
            else: 
                return jsonify({
                  "code": 500,
                  "message": f'Unable to download input file from S3: {e}'
                  }), 500
          except Exception as e:
                return jsonify({
                  "code": 500,
                  "message": f'Unknown Error while trying to downlod file from S3: {e}'
                  }), 500

          # start a new process to annotate
          run_script_path = app.config['ANNOTATOR_RUN_SCRIPT_PATH']
          try:
            print("Launching subprocess...")
            anno_process = subprocess.Popen(
            f'python {run_script_path} {user_id}/{job_id}/{file_name}',
            cwd = job_directory,
            shell = True) 
          except subprocess.SubprocessError as e:
            return jsonify({
              "code": 500,
              "message": f'Unable to open subprocess for annotation: {e}'
            }), 500
          
          # update job status to RUNNING on dynamodb
          dynamodb_resource = boto3.resource('dynamodb', region_name = app.config['AWS_REGION_NAME'])
          try: 
            table = dynamodb_resource.Table(app.config['AWS_DYNAMODB_ANNOTATIONS_TABLE'])
            table.update_item(
              Key={'job_id': msg_body["job_id"]},
              UpdateExpression='SET job_status = :val1',
              ConditionExpression='begins_with(job_status, :val2)',
              ExpressionAttributeValues={':val1': "RUNNING", ':val2': "PENDING"}
              )
          except botocore.exceptions.ClientError as e:
            code = e.response['Error']['Code']
            if code == 'ResourceNotFoundexception': 
              return jsonify({
                'code': 500, 
                'message': f'Unable to fetch dynamodb table while trying to update job status: {e}'
              }), 500
            else:
              return jsonify({
                'code': 500, 
                'message': f'Unable to update job status on DynamoDB to RUNNING: {e}'
              }), 500
          
          # Delete the message from the queue
          try: 
            print("Deleting message...")
            message.delete()
          except botocore.exceptions.ClientError as e:
            return jsonify({
              'code': 500, 
              'message': f'Unable to delete message on SQS after succefully processing the message: {e}'
              }), 500
          
        return jsonify({
          "code": 200, 
          "message": "All annotation jobs from this poll have been processed."
        }), 200
      
      else:
        return jsonify({
          "code": 500, 
          "message": "Got SNS notification but polled no message from SQS."
          }), 500
    
@app.route('/', methods=['GET'])
def home():
  return {"code": 200, "message": "Great you found your page"}

app.run('0.0.0.0', debug=True)

### EOF