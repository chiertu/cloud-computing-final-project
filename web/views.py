# views.py
#
# Copyright (C) 2011-2022 Vas Vasiliadis
# University of Chicago
#
# Application logic for the GAS
#
##
__author__ = 'Vas Vasiliadis <vas@uchicago.edu>'

import uuid
import time
import json
from decimal import Decimal
from datetime import datetime

import boto3
from botocore.client import Config
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from flask import (abort, flash, redirect, render_template, 
  request, session, url_for)

from app import app, db
from decorators import authenticated, is_premium

from auth import update_profile, get_profile

"""Start annotation request
Create the required AWS S3 policy document and render a form for
uploading an annotation input file using the policy document

Note: You are welcome to use this code instead of your own
but you can replace the code below with your own if you prefer.
"""
@app.route('/annotate', methods=['GET'])
@authenticated
def annotate():
  # Open a connection to the S3 service
  s3 = boto3.client('s3', 
    region_name=app.config['AWS_REGION_NAME'], 
    config=Config(signature_version='s3v4'))

  bucket_name = app.config['AWS_S3_INPUTS_BUCKET']
  user_id = session['primary_identity']

  # Generate unique ID to be used as S3 key (name)
  key_name = app.config['AWS_S3_KEY_PREFIX'] + user_id + '/' + \
    str(uuid.uuid4()) + '~${filename}'

  # Create the redirect URL
  redirect_url = str(request.url) + "/job"

  # Define policy conditions
  encryption = app.config['AWS_S3_ENCRYPTION']
  acl = app.config['AWS_S3_ACL']
  fields = {
    "success_action_redirect": redirect_url,
    "x-amz-server-side-encryption": encryption,
    "acl": acl
  }
  conditions = [
    ["starts-with", "$success_action_redirect", redirect_url],
    {"x-amz-server-side-encryption": encryption},
    {"acl": acl}
  ]

  # Generate the presigned POST call
  try:
    presigned_post = s3.generate_presigned_post(
      Bucket=bucket_name, 
      Key=key_name,
      Fields=fields,
      Conditions=conditions,
      ExpiresIn=app.config['AWS_SIGNED_REQUEST_EXPIRATION'])
  except ClientError as e:
    app.logger.error(f'Unable to generate presigned URL for upload: {e}')
    return abort(500)
  
  # Get user profile
  role = get_profile(identity_id=session.get('primary_identity')).role

  # Render the upload form which will parse/submit the presigned POST
  return render_template('annotate.html',
    s3_post=presigned_post,
    role=role)


"""Fires off an annotation job
Accepts the S3 redirect GET request, parses it to extract 
required info, saves a job item to the database, and then
publishes a notification for the annotator service.

Note: Update/replace the code below with your own from previous
homework assignments
"""
@app.route('/annotate/job', methods=['GET'])
@authenticated
def create_annotation_job_request():

  # https://haoyiran-a13-web.ucmpcs.org:4433/annotate/job?
  # bucket=gas-inputs
  # &key=haoyiran%2Fb47c4e9f-2603-43d2-8b1e-bab2fb9462b9%2F8ccc2c96-4ee2-495c-b625-537080340769~test.vcf
  # &etag=%22c6e5aa20fe3abc1ac9e89b84fc738fb2%22

  # Parse redirect URL query parameters for S3 object info
  bucket = request.args.get('bucket')
  key = request.args.get('key')
  _, user_id, job_id_w_file_name = key.split('/')
  job_id, file_name = job_id_w_file_name.split('~')

  # if no file is attached, abort with 400
  if file_name == "":
    return abort(409)

  # Update job status to PENDING on dynamodb
  data = {
    "job_id": job_id, 
    "user_id": user_id,
    "input_file_name": file_name,
    "s3_inputs_bucket": bucket,
    "s3_key_input_file": key,
    "submit_time": Decimal(time.time()),
    "job_status": "PENDING" 
  }

  dynamodb_resource = boto3.resource('dynamodb', region_name=app.config['AWS_REGION_NAME'])
  table = dynamodb_resource.Table(app.config['AWS_DYNAMODB_ANNOTATIONS_TABLE'])

  try:
    table.put_item(Item = data)
  except ClientError as e:
    app.logger.error(f'Unable to persist job to database: {e}')
    return abort(500)
  
  # publish a notification to SNS
  # serialize Decimal object
  # referring to: https://stackoverflow.com/questions/63278737/object-of-type-decimal-is-not-json-serializable
  class DecimalEncoder(json.JSONEncoder):
      def default(self, obj):
          if isinstance(obj, Decimal):
              return str(obj)
          return json.JSONEncoder.default(self, obj)
      
  sns_client = boto3.client("sns", region_name=app.config['AWS_REGION_NAME'])
  topicArn = app.config['AWS_SNS_JOB_REQUEST_TOPIC']
  try:
    sns_client.publish(TopicArn = topicArn,
                       Message = json.dumps(data, cls=DecimalEncoder),
                       )
  except ClientError as e:
    app.logger.error(f'Unable to send job to queue: {e}')
    return abort(500)

  return render_template('annotate_confirm.html', job_id=job_id)


"""List all annotations for the user
"""
@app.route('/annotations', methods=['GET'])
@authenticated
def annotations_list():
  dynamodb_client = boto3.client('dynamodb', region_name=app.config['AWS_REGION_NAME'])
  try: 
    response = dynamodb_client.query(
      TableName=app.config['AWS_DYNAMODB_ANNOTATIONS_TABLE'], 
      IndexName="user_id_index",
      Select='SPECIFIC_ATTRIBUTES', 
      ProjectionExpression="job_id, submit_time, input_file_name, job_status", 
      KeyConditionExpression="user_id = :u", 
      ExpressionAttributeValues={":u": {"S": session['primary_identity']}}
      )
  except ClientError as e: 
    app.logger.error(f'Unable to get job lists from database: {e}')
    abort(500)
  
  job_list = []
  for item in response['Items']:
    job = {}
    job['job_id'] = item['job_id']['S']
    # referring to: https://docs.python.org/3/library/time.html#time.localtime
    submit_time_object = time.localtime(float(item['submit_time']['N']))
    # referring to: https://www.pythoncentral.io/how-to-get-and-format-the-current-time-in-python/
    job['submit_time'] = time.strftime('%Y-%m-%d %H:%M', submit_time_object)
    job['input_file_name'] = item['input_file_name']['S']
    job['job_status'] = item['job_status']['S']
    job_list.append(job)
  job_list.reverse()
  return render_template('annotations.html', job_list = job_list)


"""Display details of a specific annotation job
"""
@app.route('/annotations/<id>', methods=['GET'])
@authenticated
# pass variable from route
# https://stackoverflow.com/questions/35188540/get-a-variable-from-the-url-in-a-flask-route
def annotation_details(id):
  try:
    dynamodb_client = boto3.client('dynamodb', region_name=app.config['AWS_REGION_NAME'])
    response = dynamodb_client.get_item(
       TableName=app.config['AWS_DYNAMODB_ANNOTATIONS_TABLE'], 
       Key={'job_id': {'S': id}})
    # throw ResourceNotFoundException
    # referring to: https://stackoverflow.com/a/68521788/8527838
  except dynamodb_client.exceptions.ResourceNotFoundException:
    app.logger.error(f'Unable to locate recource in database: {e}')
    abort(500)   
  
  try:
    job = response['Item']
  except KeyError: 
    app.logger.error(f'Unable to locate job in database: {e}')
    abort(404)
  if job['user_id']['S'] != session['primary_identity']: 
    abort(403)
  job_details = {}
  job_details['job_id'] = job['job_id']['S']
  submit_time_object = time.localtime(float(job['submit_time']['N']))
  job_details['submit_time'] = time.strftime('%Y-%m-%d @ %H:%M:%S', submit_time_object)

  # download input file
  s3_client = boto3.client('s3', region_name=app.config['AWS_REGION_NAME'], config=Config(signature_version='s3v4'))
  try:
    input_response = s3_client.generate_presigned_url(
      ClientMethod='get_object', 
      Params={'Bucket': app.config["AWS_S3_INPUTS_BUCKET"], 
              'Key': job['s3_key_input_file']['S']}, 
              ExpiresIn = 120)
  except ClientError as e: 
    app.logger.error(f'Unable to download input file from S3: {e}')
    abort(500)
  job_details['input_file_url'] = input_response
  job_details['input_file_name'] = job['input_file_name']['S']
  job_details['job_status'] = job['job_status']['S']


  # download file for user if job completed
  if job_details['job_status'] == 'COMPLETED': 
    # complete_time
    complete_time_float = float(job['complete_time']['N'])
    complete_time_object = time.localtime(complete_time_float) 
    job_details['complete_time'] = time.strftime('%Y-%m-%d @ %H:%M:%S', complete_time_object)

    # s3_key_log_file
    job_details['s3_key_log_file'] = job['s3_key_log_file']['S']

    # free_access_expired
    if session['role'] == 'premium_user' and 's3_key_result_file' not in job.keys(): 
      job_details['restore_msg'] = True
      # restore logic
    if 's3_key_result_file' in job.keys():
      # result_file_url
      try:
        result_response = s3_client.generate_presigned_url(
          ClientMethod='get_object', 
          Params={'Bucket': app.config["AWS_S3_RESULTS_BUCKET"], 
                  'Key': job['s3_key_result_file']['S']}, 
                  ExpiresIn = 120)
      except ClientError as e: 
        app.logger.error(f'Unable to download result file from S3: {e}')
        abort(500)
      job_details['result_file_url'] = result_response
    
  return render_template('annotation.html', job_details = job_details)


"""Display the log file contents for an annotation job
"""
@app.route('/annotations/<id>/log', methods=['GET'])
@authenticated
def annotation_log(id):
  try:
    print(id)
    dynamodb_client = boto3.client('dynamodb', region_name=app.config['AWS_REGION_NAME'])
    response = dynamodb_client.get_item(
       TableName=app.config['AWS_DYNAMODB_ANNOTATIONS_TABLE'], 
       Key={'job_id': {'S': id}})
    # throw ResourceNotFoundException
    # referring to: https://stackoverflow.com/a/68521788/8527838
  except dynamodb_client.exceptions.ResourceNotFoundException:
    app.logger.error(f'Unable to locate recource in database: {e}')
    abort(500)
  
  try:
    job = response['Item']
  except KeyError: 
    app.logger.error(f'Unable to locate job in database: {e}')
    abort(404)
  if job['user_id']['S'] != session['primary_identity']: 
    abort(403)
  s3_key_log_file = job['s3_key_log_file']['S']

  s3_client = boto3.client('s3', region_name=app.config['AWS_REGION_NAME'])
  try: 
    response = s3_client.get_object(
      Bucket=app.config['AWS_S3_RESULTS_BUCKET'],
      Key=s3_key_log_file)
  except ClientError as e: 
    app.logger.error(f'Unable to locate file on S3: {e}')
    code = e.response['Error']['Code']
    if code == 'NoSuchKey' or code == 'NoSuchBucket': 
      abort(404)
    else: 
      abort(500)

  log_file_contents = response['Body'].read().decode()

  return render_template('view_log.html', job_id = id, log_file_contents = log_file_contents)


"""Subscription management handler
"""
import stripe

@app.route('/subscribe', methods=['GET', 'POST'])
@authenticated
def subscribe():
  if (request.method == 'GET'):
    # Display form to get subscriber credit card info
    if (session.get('role') == "free_user"):
      return render_template('subscribe.html')
    else:
      return redirect(url_for('profile'))
    
  elif (request.method == 'POST'):
    stripe.api_key = app.config['STRIPE_SECRET_KEY']
    # Process the subscription request
    token = request.form['stripe_token']
    
    # Create a customer on Stripe
    # referring to: https://stripe.com/docs/api/customers/create
    try:
      customer = stripe.Customer.create(
          source=token,
          name=session.get('name'),
          email=session.get('email')
      )
    except stripe.error.StripeError as e:
      app.logger.error(f'Unable to create customer on Stripe: {e}')
      return abort(500)

    # Subscribe customer to pricing plan
    try:
      subscription = stripe.Subscription.create(
          customer=customer.id,
          items=[
            {"price": app.config['STRIPE_PRICE_ID']},
          ],
        )
    except stripe.error.StripeError as e:
      app.logger.error(f'Unable to subscribe customer to premium plan: {e}')
      return abort(500)

    # Update user role in accounts database
    update_profile(
      identity_id=session['primary_identity'],
      role="premium_user"
      )

    # Update role in the session
    session['role'] == "premium_user"


    # Request restoration of the user's data from Glacier
    # ...add code here to initiate restoration of archived user data
    # ...and make sure you handle files not yet archived!
    sns_client = boto3.client("sns", region_name=app.config['AWS_REGION_NAME'])
    topicArn_did_upgrade = app.config['AWS_SNS_DID_UPGRADE_TOPIC']
    try:
      sns_client.publish(TopicArn = topicArn_did_upgrade,
                         Message = json.dumps({'user_id': session['primary_identity']}),
                        )
    except ClientError as e:
      app.logger.error(f'Unable to request thawing Glacier archives: {e}')
      return abort(500)


    # Display confirmation page
    return render_template('subscribe_confirm.html', stripe_id = customer.id)


"""Set premium_user role
"""
@app.route('/make-me-premium', methods=['GET'])
@authenticated
def make_me_premium():
  # Hacky way to set the user's role to a premium user; simplifies testing
  update_profile(
    identity_id=session['primary_identity'],
    role="premium_user"
  )
  return redirect(url_for('profile'))


"""Reset subscription
"""
@app.route('/unsubscribe', methods=['GET'])
@authenticated
def unsubscribe():
  # Hacky way to reset the user's role to a free user; simplifies testing
  update_profile(
    identity_id=session['primary_identity'],
    role="free_user"
  )
  return redirect(url_for('profile'))


"""DO NOT CHANGE CODE BELOW THIS LINE
*******************************************************************************
"""

"""Home page
"""
@app.route('/', methods=['GET'])
def home():
  return render_template('home.html')

"""Login page; send user to Globus Auth
"""
@app.route('/login', methods=['GET'])
def login():
  app.logger.info(f"Login attempted from IP {request.remote_addr}")
  # If user requested a specific page, save it session for redirect after auth
  if (request.args.get('next')):
    session['next'] = request.args.get('next')
  return redirect(url_for('authcallback'))


"""400 error handler
"""
@app.errorhandler(409)
def bad_request(e):
  return render_template('error.html', 
    title='Attachement not found', alert_level='warning',
    message="You have to attach a vcf file. Return to the previous page, refresh the page and try again."
    ), 409

"""404 error handler
"""
@app.errorhandler(404)
def page_not_found(e):
  return render_template('error.html', 
    title='Page not found', alert_level='warning',
    message="The page you tried to reach does not exist. \
      Please check the URL and try again."
    ), 404

"""403 error handler
"""
@app.errorhandler(403)
def forbidden(e):
  return render_template('error.html',
    title='Not authorized', alert_level='danger',
    message="You are not authorized to access this page. \
      If you think you deserve to be granted access, please contact the \
      supreme leader of the mutating genome revolutionary party."
    ), 403

"""405 error handler
"""
@app.errorhandler(405)
def not_allowed(e):
  return render_template('error.html',
    title='Not allowed', alert_level='warning',
    message="You attempted an operation that's not allowed; \
      get your act together, hacker!"
    ), 405

"""500 error handler
"""
@app.errorhandler(500)
def internal_error(error):
  return render_template('error.html',
    title='Server error', alert_level='danger',
    message="The server encountered an error and could \
      not process your request."
    ), 500

### EOF