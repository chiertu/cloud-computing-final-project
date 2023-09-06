# thaw_app.py
#
# Thaws upgraded (premium) user data
#
# Copyright (C) 2011-2021 Vas Vasiliadis
# University of Chicago
##
__author__ = 'Vas Vasiliadis <vas@uchicago.edu>'

import json
import os
import boto3
import botocore

from flask import Flask, request, jsonify
import requests

app = Flask(__name__)
environment = 'thaw_app_config.Config'
app.config.from_object(environment)
app.url_map.strict_slashes = False

@app.route('/', methods=['GET'])
def home():
  return (f"This is the Thaw utility: POST requests to /thaw.")

@app.route('/thaw', methods=['POST'])
def thaw_premium_user_data():
    if request.method == 'POST':
        # get SQS queue, exit if cannot get queue
        sqs_resource = boto3.resource("sqs", region_name = app.config['AWS_REGION_NAME'])
        try:
            queue = sqs_resource.get_queue_by_name(QueueName = app.config['AWS_SQS_DID_UPGRADE_QUEUE_NAME'])
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
                print(f'Received {str(len(messages))} user_did_upgrade messages...')

                # Iterate each message
                for message in messages:
                    # Parse JSON message
                    print("new user_did_upgrade message")
                    try:
                        msg_body = json.loads(json.loads(message.body)["Message"])
                        print(msg_body)
                    except json.JSONDecodeError as e:
                        print(e)
                        return jsonify({
                            "code": 500,
                            "message": f'Unable to decode message into json: {e}'
                            }), 500
                    
                    # get user_id 
                    try: 
                        user_id = msg_body["user_id"]
                    except KeyError as e:
                        print(e)
                        return jsonify({
                            "code": 500,
                            "message": f'Missing field in message: {e}'
                            }), 500
                    
                    # get a list of archive id for user_id
                    dynamodb_client = boto3.client('dynamodb', region_name=app.config['AWS_REGION_NAME'])
                    try: 
                        print(f'Retrieving a list of archive id for user: {user_id}')
                        response = dynamodb_client.query(
                            TableName=app.config['AWS_DYNAMODB_ANNOTATIONS_TABLE'], 
                            IndexName="user_id_index",
                            Select='SPECIFIC_ATTRIBUTES', 
                            ProjectionExpression="results_file_archive_id, job_id", 
                            KeyConditionExpression="user_id = :u", 
                            ExpressionAttributeValues={":u": {"S": user_id}}
                            )
                    except botocore.exceptions.ClientError as e:
                        print(e)
                        return jsonify({
                            "code": 500,
                            "message": f'Unable to get archive id from dynamodb: {e}'
                            }), 500
                    

                    glacier_client = boto3.client('glacier', region_name=app.config['AWS_REGION_NAME'])
                    archive_retrieved_sns_topic = app.config['AWS_SNS_ARCHIVE_RETRIEVED_TOPIC']
                    vault_name = app.config['AWS_GLACIER_VAULT_NAME']
                    # iterate through archive id
                    print("Iterating through archive ids")
                    for item in response['Items']:
                        print("another archive id")
                        if "results_file_archive_id" not in item: continue
                        archive_id = item['results_file_archive_id']['S']
                        job_id = item['job_id']['S']
                        try:
                            # Send expedited retrieval request
                            response = glacier_client.initiate_job(
                                vaultName=vault_name,  
                                jobParameters={
                                    'Type': 'archive-retrieval',
                                    'ArchiveId': archive_id,
                                    'Tier': 'Expedited',
                                    'SNSTopic': archive_retrieved_sns_topic
                                }
                            )
                        except glacier_client.exceptions.InsufficientCapacityException as e:
                            # Catch and print InsufficientCapacityException
                            print('Error: Insufficient capacity:', e)
                            
                            # Try standard retrieval
                            print('Trying standard retrieval...')
                            try:
                                response = glacier_client.initiate_job(
                                    vaultName=vault_name, 
                                    jobParameters={
                                        'Type': 'archive-retrieval',
                                        'ArchiveId': archive_id,
                                        'Tier': 'Standard',
                                        'SNSTopic': archive_retrieved_sns_topic,
                                        'Description': job_id
                                    }
                                )
                                print(response['jobId'])
                            # generic glacier exception
                            # referring to: https://sdk.amazonaws.com/java/api/latest/software/amazon/awssdk/services/glacier/GlacierClient.html
                            except glacier_client.exceptions.GlacierException as e:
                                print(e)
                                return jsonify({
                                    "code": 500,
                                    "message": f'Unable to start standard archive retrieval through glacier: {e}'
                                    }), 500
                        except glacier_client.exceptions.GlacierException as e:
                            print(e)
                            return jsonify({
                                "code": 500,
                                "message": f'Unexpected error while starting expedited retrieval through glacier: {e}'
                                }), 500
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
                    "message": "All user upgrade jobs from this poll have been processed."
                    }), 200
                        
            else:
                return jsonify({
                    "code": 500, 
                    "message": "Got SNS notification but polled no message from SQS."
                    }), 500
                        



# Run using dev server (remove if running via uWSGI)
app.run('0.0.0.0', port=5001, debug=True)


### EOF



### user decide to upgrade
### at the end of the script that is upgrading the user, send an SNS (user_id)

### /util endpoint receive SNS
### query dnamodb with user_id get archive_id
  ### attempt expedited retrieval
  ### get an insufficientexception
  ### use standard retrieval

### /lambda endpoint receive SNS (????? structure of SNS)
### 1.receive SNS from glacier
### 2.use archive_id to find user_id, job_id, file_name in dynamodb and reconstructs s3_result_file
### 3.copy temporary s3 file to gas-results se_result_file_path

