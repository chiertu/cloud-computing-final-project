# run.py
#
# Copyright (C) 2011-2019 Vas Vasiliadis
# University of Chicago
#
# Wrapper script for running AnnTools
#
##
__author__ = 'Vas Vasiliadis <vas@uchicago.edu>'

from flask import Flask, request, jsonify, Response
import sys
import time
import driver
import boto3
import botocore
import os
import json
from decimal import Decimal
from configparser import ConfigParser

# A rudimentary timer for coarse-grained profiling
class Timer(object):
  def __init__(self, verbose=True):
    self.verbose = verbose

  def __enter__(self):
    self.start = time.time()
    return self

  def __exit__(self, *args):
    self.end = time.time()
    self.secs = self.end - self.start
    if self.verbose:
      print(f"Approximate runtime: {self.secs:.2f} seconds")


# read config file
config = ConfigParser(os.environ)
config.read('/home/ubuntu/gas/ann/ann_config.ini')
base_directory = config['ann']['ANNOTATOR_BASE_DIR']
result_bucket = config['s3']['AWS_S3_RESULTS_BUCKET']



def main(file_path):
	# variables for file path
	user_id, job_id, file_name = file_path.split("/")
	logfile_name = file_name + ".count.log"
	annofile_name = file_name[:-4] + ".annot.vcf"
	
	job_directory = base_directory + "jobs/" + user_id + "/" + job_id
	inputfile_path = job_directory + "/" + file_name
	logfile_path = job_directory + "/" + logfile_name
	logfile_path_s3 = "haoyiran/" + user_id + "/" + job_id + "~" + logfile_name
	annofile_path = job_directory + "/" + annofile_name
	annofile_path_s3 = "haoyiran/" + user_id + "/" + job_id + "~" + annofile_name  
	
	with Timer():
		try:
			driver.run(inputfile_path, 'vcf')
		except FileNotFoundError as e: 
			print({
                'code': 404, 
                'status': 'NotFound', 
                'message': f'FileNotFoundError: {inputfile_path}',
            })
			return


	# if job dir,log file, annotation file do not exist, return
	if not (os.path.exists(job_directory) and os.path.isfile(logfile_path) and os.path.isfile(annofile_path)):
		print({
			'code': 500,
			'status': 'ResultsFilesMissing',
			'message': f'Failed to produce results files for: {inputfile_path}',
		}) 
		return
		
	# upload log and annotation files on S3
	s3_resource = boto3.resource('s3', region_name = config['aws']['AWS_REGION_NAME'])
	try:
		s3_resource.meta.client.upload_file(logfile_path, result_bucket, logfile_path_s3)
		s3_resource.meta.client.upload_file(annofile_path, result_bucket, annofile_path_s3)
	except botocore.exceptions.ClientError as e:
		print({
			'code': 500,
			'status': 'S3UploadFailed',
			'message': f'Failed to upload results files for: {inputfile_path}',
		})
		return
	except FileNotFoundError as e: 
		print({
			'code': 404, 
			'status': 'NotFound', 
			'message': f'FileNotFoundError: {inputfile_path}',
		})
		return
	except Exception as e:
		print(e)
		return
	

	# update job status to COMPLETED on dynamodb
	dynamodb_resource = boto3.resource('dynamodb', region_name = config['aws']['AWS_REGION_NAME'])
	try: 
		table = dynamodb_resource.Table(config['dynamodb']['AWS_DYNAMODB_ANNOTATIONS_TABLE'])
		table.update_item(
			Key={'job_id': job_id},
			UpdateExpression='SET job_status = :val1, s3_results_bucket = :val2, s3_key_result_file = :val3, s3_key_log_file = :val4, complete_time = :val5',
			ExpressionAttributeValues={
				':val1': "COMPLETED",
				':val2': result_bucket,
				':val3': annofile_path_s3,
				':val4': logfile_path_s3,
				':val5': Decimal(time.time())})
	except botocore.exceptions.ClientError as e:
		code = e.response['Error']['Code']
		if code == 'ResourceNotFoundexception': 
			print({
				'code': 404, 
				'status': 'RequestedResourceNotFound', 
				'message': f'Failed to fetch dynamodb table: {e}'
				})
			return
		else:
			print({
				'code': 500, 
				'status': 'ServerError', 
				'message': f'{e}'
			})
			return
	
	# trigger step function
	# referring to: https://www.youtube.com/watch?v=s0XFX3WHg0w
	msg_step = {}
	msg_step["user_id"] = user_id
	msg_step["job_id"] = job_id
	msg_step["results_bucket"] = result_bucket
	msg_step["annofile_path_s3"] = annofile_path_s3
	stepfunction_client = boto3.client('stepfunctions', region_name = config['aws']['AWS_REGION_NAME'])
	try: 
		response = stepfunction_client.start_execution(
			stateMachineArn = config["stepfunction"]["AWS_STEPFUNCTION_WAITING_ENGINE"],
			name=f'{job_id}-stepfunctions',
			input=json.dumps(msg_step),
			traceHeader='string'
		)
	except botocore.exceptions.ClientError as e:
		if e.response['Error']['Code'] == 'ExecutionAlreadyExists':
			print({
				'code': 500, 
				'message': f'Execution already exists, cannot start a new execution'
				})
			return
		elif e.response['Error']['Code'] == 'StateMachineNotRunning':
			print({
				'code': 500, 
				'message': f'State machine is not in a running state, cannot start a new execution'
				})
			return
		else:
			print({
				'code': 500, 
				'message': f'Error starting Step Function execution: {e}'
				})
			return
	except Exception as e:
		print({
			'code': 500, 
			'message': f'Unexpected Error: {e}'
			})
		return
		
	# delete job directory from local instance 
	try: 
		os.remove(logfile_path)
		os.remove(annofile_path)
		os.remove(inputfile_path)
		os.rmdir(job_directory)
	except OSError as e:
		print({
			'code': 500,
			'status': 'OSError',
			'message': f'OSError: {e}'
		})
		return


if __name__ == '__main__':
	# Call the AnnTools pipeline
	if len(sys.argv) > 1:
		main(sys.argv[1])

	else:
		print("A valid .vcf file must be provided as input to this program.")
 

### EOF