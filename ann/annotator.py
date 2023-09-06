import subprocess
import os
import boto3
from botocore.exceptions import ClientError
import json
from configparser import ConfigParser

if __name__ == '__main__':
    config = ConfigParser(os.environ)
    config.read('/home/ubuntu/gas/ann/ann_config.ini')


    base_directory = config['ann']['ANNOTATOR_BASE_DIR']
    job_directory = config['ann']['ANNOTATOR_JOBS_DIR']
    run_script_path = config['ann']['ANNOTATOR_RUN_SCRIPT_PATH']

    # Connect to SQS and get queue
    sqs_resource = boto3.resource("sqs", region_name = config['aws']['AWS_REGION_NAME'])

    try:
        queue = sqs_resource.get_queue_by_name(QueueName = config['sqs']['AWS_SQS_NAME'])
    except ClientError as e:
        print(f'Getting message queue from SQS failed: {str(e.response)}')
    else: 
        while True:
            print("Asking SQS for up to 10 messages.")
            # Get messages
            try: 
                wait_time = int(config['sqs']['AWS_SQS_WAIT_TIME'])
                max_num_message = int(config['sqs']['AWS_SQS_MAX_MESSAGES'])
                messages = queue.receive_messages(WaitTimeSeconds = wait_time, 
                                                  MaxNumberOfMessages = max_num_message)
            except ClientError as error:
                print(f'Receiving message from SQS failed: {str(e.response)}')
            else:
                if len(messages) > 0:
                    print(f'Received {str(len(messages))} messages...')
                    # Iterate each message
                    for message in messages:

                        try: 
                            # Parse JSON message
                            print("new message")
                            msg_body = json.loads(json.loads(message.body)["Message"])
                            bucket = msg_body["s3_inputs_bucket"]
                            fileKey = msg_body["s3_key_input_file"]
                            fileName = msg_body["input_file_name"]
                            jobID = msg_body["job_id"]
                            userID = msg_body["user_id"]

                            # create user and job folder
                            user_does_exist = os.path.exists(f'{job_directory}/{userID}')
                            if not user_does_exist:
                                os.makedirs(f'{job_directory}/{userID}')
                            job_does_exist = os.path.exists(f'{job_directory}/{userID}/{jobID}')
                            if not job_does_exist:
                                os.makedirs(f'{job_directory}/{userID}/{jobID}')

                            # download file from s3
                            # referring to documentation: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html#S3.Client.download_file
                            s3_resource = boto3.resource('s3', region_name = config['aws']['AWS_REGION_NAME'])
                            s3_resource.meta.client.download_file(bucket, fileKey, f'{job_directory}/{userID}/{jobID}/{fileName}')

                            # start a new process to annotate
                            anno_process = subprocess.Popen(
                                f'python {run_script_path} {userID}/{jobID}/{fileName}',
                                cwd = job_directory,
                                shell = True) 
                        except ClientError as boto3_e:
                            print(f'Downloading from S3 failed: {boto3_e.response}') 
                        # subprocess exception list
                        # referring to: https://docs.python.org/3/library/subprocess.html
                        except subprocess.SubprocessError as subprocess_e:
                            print("Opening subprocess failed: " + str(subprocess_e))
                        except OSError as os_e:
                            print("OSError ocurred: " + str(os_e))
                        except Exception as other_e:
                            print("Other error: " + str(other_e))
                        else:
                            # update job status on db to RUNNING
                            dynamodb_resource = boto3.resource('dynamodb', region_name = config['aws']['AWS_REGION_NAME'])
                            table = dynamodb_resource.Table(config['dynamodb']['AWS_DYNAMODB_ANNOTATIONS_TABLE'])
                            try: 
                                table.update_item(
                                    Key={'job_id': jobID},
                                    UpdateExpression='SET job_status = :val1',
                                    ConditionExpression='begins_with(job_status, :val2)',
                                    ExpressionAttributeValues={':val1': "RUNNING", ':val2': "PENDING"}
                                    )
                            except ClientError as e:
                                print(f'Updating job status on DynamoDB to RUNNING failed: {e.response}')
                            else: 
                                try: 
                                    # Delete the message from the queue
                                    print("Deleting message...")
                                    message.delete()
                                except ClientError as e:
                                    print(f'Deleting SQS message failed: {e.response}')