# archive_app.py
#
# Archive free user data
#
# Copyright (C) 2011-2021 Vas Vasiliadis
# University of Chicago
##
__author__ = 'Vas Vasiliadis <vas@uchicago.edu>'

from flask import Flask, request, jsonify
import boto3
import botocore
import json
import requests
import os
import sys

# Import utility helpers
sys.path.insert(1, os.path.realpath(os.path.pardir))
import helpers

app = Flask(__name__)
environment = 'archive_app_config.Config'
app.config.from_object(environment)


@app.route('/', methods=['GET'])
def home():
    return (f"This is the Archive utility: POST requests to /archive.")

@app.route('/archive', methods=['POST'])
def archive_free_user_data():
    if request.method == 'POST':
         # get SQS queue, exit if cannot get queue
        sqs_resource = boto3.resource("sqs", region_name = app.config['AWS_REGION_NAME'])
        try:
            queue = sqs_resource.get_queue_by_name(QueueName = app.config['AWS_SQS_WAIT_ENDED_QUEUE_NAME'])
        except botocore.exceptions.ClientError as e:
            print(e)
            return jsonify({
                "code": 503,
                "message": f'Unable to get SQS queue: {e}'
                }), 503
        

        # Check header for message type
        try:
            js = json.loads(request.data)
        except json.JSONDecodeError as e:
            print(e)
            return jsonify({
                "code": 500,
                "message": f'Unable to decode json data: {e}'
            }), 500
        
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
                print(e)
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
                    except json.JSONDecodeError as e:
                        print(e)
                        return jsonify({
                            "code": 500,
                            "message": f'Unable to decode message into json: {e}'
                            }), 500
                    
                    try: 
                        job_id = msg_body["job_id"]
                        user_id = msg_body["user_id"]
                        results_bucket = msg_body["results_bucket"]
                        annofile_path_s3 = msg_body["annofile_path_s3"]
                    except KeyError as e:
                        print(e)
                        return jsonify({
                            "code": 500,
                            "message": f'Missing field in message: {e}'
                            }), 500
                    
                    # get user_id profile
                    _, _, _, _, role, _, _ = helpers.get_user_profile(id = user_id) 
                    # If premium user, delete the message from the queue
                    if role == 'premium_user':
                        print("premium user")
                        try: 
                            print("Deleting message...")
                            message.delete()
                        except botocore.exceptions.ClientError as e:
                            print(e)
                            return jsonify({
                            'code': 500, 
                            'message': f'Unable to delete message on SQS after succefully processing the message: {e}'
                            }), 500
                    
                    else:
                        print("free user")
                        
                        # Retrieve the S3 object
                        s3_resource = boto3.resource('s3', region_name=app.config['AWS_REGION_NAME'])
                        try:
                            annofile = s3_resource.Object(results_bucket, annofile_path_s3)
                        except botocore.exceptions.ClientError as e:
                            print(e)
                            error_code = e.response['Error']['Code']
                            if 'NoSuchBucket' in error_code or 'NoSuchKey' in error_code:
                                return jsonify({
                                    'code': 400, 
                                    'message': f'Unable to find result file on S3: {e}'
                                    }), 400
                            else:
                                return jsonify({
                                    'code': 500, 
                                    'message': f'Unexpected error while getting result file from S3: {e}'
                                    }), 500


                        # Upload the S3 object to Glacier and get archive id
                        glacier_client = boto3.client('glacier', region_name=app.config['AWS_REGION_NAME'])
                        try:
                            print("moving result file to Glacier")
                            response = glacier_client.upload_archive(
                                vaultName=app.config['AWS_GLACIER_VAULT_NAME'],
                                archiveDescription=job_id,
                                body=annofile.get()['Body'].read()
                            )
                            print(response)
                            archive_id = response['archiveId']
                        except botocore.exceptions.ClientError as e:
                            print(e)
                            error_code = e.response['Error']['Code']
                            if 'ResourceNotFoundException' in error_code or 'InvalidParameterValueException' in error_code:
                                return jsonify({
                                    'code': 400,
                                    'message': f'Unable to locate vault on S3 Glacier: {e}'
                                }), 400
                            else:
                                return jsonify({
                                    'code': 500,
                                    'message': f'Unexpected error while uploading file to S3 Glacier: {e}'
                                }), 500
                        

                        # Update Dynamodb with Glacier key
                        dynamodb_resource = boto3.resource('dynamodb', region_name=app.config['AWS_REGION_NAME'])
                        try:
                            print("persisting archive id to dynamodb")
                            table = dynamodb_resource.Table(app.config['AWS_DYNAMODB_ANNOTATIONS_TABLE'])
                            response = table.update_item(
                                Key={'job_id': job_id},
                                UpdateExpression='SET results_file_archive_id = :val1 REMOVE s3_key_result_file',
                                ExpressionAttributeValues={
                                    ':val1': archive_id
                                }
                            )
                        except botocore.exceptions.ClientError as e:
                            print(e)
                            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                                return jsonify({
                                    'code': 400,
                                    'message': f'Unable to update because the condition was not met: {e}'
                                }), 400
                            elif e.response['Error']['Code'] == 'ItemNotFoundException':
                                return jsonify({
                                    'code': 404,
                                    'message': f'Unable to find the item: {e}'
                                }), 404
                            else:
                                return jsonify({
                                    'code': 500,
                                    'message': f'Unexpected error while updating dynamodb: {e}'
                                }), 500
                            

                        # Remove file from S3
                        # referring to: https://stackoverflow.com/questions/3140779/how-to-delete-files-from-amazon-s3-bucket
                        print("deleting result file on S3")
                        annofile.delete()
                        try: 
                            print("Deleting message...")
                            message.delete()
                        except botocore.exceptions.ClientError as e:
                            print(e)
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
       

                   
    
# Run using dev server (remove if running via uWSGI)
app.run('0.0.0.0', debug=True)
### EOF