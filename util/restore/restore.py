# restore.py
#
# Restores thawed data, saving objects to S3 results bucket
# NOTE: This code is for an AWS Lambda function
#
# Copyright (C) 2011-2021 Vas Vasiliadis
# University of Chicago
##

import boto3
import json
import botocore

# Define constants here; no config file is used for Lambdas
AWS_DYNAMODB_ANNOTATIONS_TABLE = "haoyiran_annotations"
AWS_REGION_NAME = "us-east-1"
AWS_GLACIER_VAULT_NAME = "ucmpcs"
AWS_S3_RESULTS_BUCKET = "gas-results"


def lambda_handler(event, context):
    # print("Received event: " + json.dumps(event, indent=2))
    
    # parse message 
    # referring to: https://docs.aws.amazon.com/lambda/latest/dg/with-sns-example.html
    msg_body = json.loads(event['Records'][0]['Sns']['Message'])
    archive_job_id = msg_body['JobId']
    job_id = msg_body['JobDescription']
    status_code = msg_body['StatusCode']
    archive_id = msg_body['ArchiveId']
    
    # archive_job_id = 'JzWbdwV9NoF8uE7lXv21yJuEb_VAbRYYr46y5MlKtuC052S4Ahv2cqPm9EPx8lLISsD4nNm5gINUpUg3Fof2JbgmCV1p'
    # # 'zS70JNvTaEVUxF0OR9W6oBpVsTnCP5Iu8wtnnwo5xVvYGmbH4VHqnRjecy_LoglDvWUpQuk85qN6VaXHdUFrAV6X5IjW1rq546TKa_d774wPSK8InQDLNoPNHEQTNI2g1q6Fh7_JCA'
    # job_id = '4b681288-b4dd-4161-9d4b-968eead16e2d'
    # status_code = "Succeeded"
    
    # if retrieval job failed, return
    if status_code != "Succeeded":
        print("Archive retrieval job failed")
        return {
            'statusCode': 500,
            'body': "Archive retrieval job failed"
            }
    
    # if retrieval job succeeded
    # use job_id to query dynamodb
    dynamodb_client = boto3.client('dynamodb', region_name=AWS_REGION_NAME)
    try:
        print("Getting information from dynamodb with job_id")
        response_dynamodb = dynamodb_client.get_item(
            TableName=AWS_DYNAMODB_ANNOTATIONS_TABLE, 
            Key={'job_id': {'S': job_id}})
    # throw ResourceNotFoundException
    # referring to: https://stackoverflow.com/a/68521788/8527838
    except dynamodb_client.exceptions.ResourceNotFoundException as e:
        print(f'Unable to locate job in database: {e}')
        return {
            'statusCode': 500,
            'body': f'Unable to locate job in database: {e}'
            }
    try:
        job = response_dynamodb['Item']
    except KeyError: 
        print(f'Unable to locate job in database: {e}')
        return {
            'statusCode': 500,
            'body': f'Unable to locate job in database: {e}'
            }

    # Constructing new s3 result key
    user_id = job['user_id']['S']
    input_file_name = job['input_file_name']['S']
    result_file_name = input_file_name[:-4] + ".annot.vcf"
    new_result_key = f'haoyiran/{user_id}/{job_id}~{result_file_name}'


    # download file from glacier
    # referring to: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/glacier/client/get_job_output.html
    glacier_client = boto3.client('glacier', region_name = AWS_REGION_NAME)
    try:
        print("Downloading file from Glacier")
        response_glacier = glacier_client.get_job_output(
            jobId=archive_job_id,
            vaultName=AWS_GLACIER_VAULT_NAME,
        )
    except botocore.exceptions.BotoCoreError as e:
        print(f'Unable to get job description with archive retrieval job id: {e}')
        return {
            'statusCode': 500,
            'body': f'Unable to get job description with archive retrieval job id: {e}'
            }

    # upload file to s3
    # referring to: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3/client/upload_fileobj.html
    s3_client = boto3.client('s3', region_name=AWS_REGION_NAME)
    try:
        print("Uploading file to S3")
        s3_client.upload_fileobj(response_glacier['body'], 
                                 AWS_S3_RESULTS_BUCKET, 
                                 new_result_key)
    except botocore.exceptions.ClientError as e:
        print(f'Unable to upload retrieved file to S3: {e}')
        return {
            'statusCode': 500,
            'body': f'Unable to upload retrieved file to S3: {e}'
            }
    except botocore.exceptions.EndpointConnectionError as e:
        return {
            'statusCode': 500,
            'body': f'Unable to upload retrieved file to S3 due to network issues: {e}'
            }
    
    # updating dynamodb
    dynamodb_resource = boto3.resource('dynamodb', region_name = AWS_REGION_NAME)
    try:
        print("Updating dynamodb")
        table = dynamodb_resource.Table(AWS_DYNAMODB_ANNOTATIONS_TABLE)
        response = table.update_item(
            Key={'job_id': job_id},
            UpdateExpression='REMOVE results_file_archive_id SET s3_key_result_file = :val1',
            ExpressionAttributeValues={
                ':val1': new_result_key
            }
        )
    except botocore.exceptions.ClientError as e:
        print(f'Unable to update dynamodb table with new s3 key: {e}')
        return {
        'code': 500, 
        'body': f'Unable to update dynamodb table with new s3 key: {e}'
        }

    # deleting archive
    # referring to: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/glacier/client/delete_archive.html
    print("Deleting archive files")
    try:
        response = glacier_client.delete_archive(
            vaultName=AWS_GLACIER_VAULT_NAME,
            archiveId=archive_id
        )
    except botocore.exceptions.BotoCoreError as e:
        print(f'Unable to delete archive: {e}')
        return {
            'statusCode': 500,
            'body': f'Unable to delete archive: {e}'
            }

    return {
        'statusCode': 200,
        'body': "Archived restul files are back in S3"
    }
