# GAS Framework
An enhanced web framework (based on [Flask](https://flask.palletsprojects.com/)) for use in the capstone project. Adds robust user authentication (via [Globus Auth](https://docs.globus.org/api/auth)), modular templates, and some simple styling based on [Bootstrap](https://getbootstrap.com/docs/3.3/).

Directory contents are as follows:
* `/web` - The GAS web app files
* `/ann` - Annotator files
* `/util` - Utility scripts/apps for notifications, archival, and restoration
* `/aws` - AWS user data files


# Implementing wait
## resources
3 new resources
- a step function called haoyiran_a14_waiting_engine
- an SNS called haoyiran_a14_wait_ended
- an SQS called haoyiran_a14_wait_ended

## workflow
/haoyiran-a14-ann (ec2)
- after annotation finishes, start an execution of the step function and pass along job information as json 

/haoyiran_a14_waiting_engine (step function)
- wait for 5 min 
- take the json input, put it in a message and publish an SNS message to /util endpoint

/haoyiran-a14-util (ec2)
- receive the SNS message
- poll message from SQS and parse information
- if user_id is free account
    a. move S3 to Glacier
    b. delete S3 file
    c. update dynamodb, delte s3 key, put glacier key


# Implementing restore
## resources
- a new endpoint called /thaw
- an SNS called haoyiran_a16_did_upgrade
- an SQS called haoyiran_a16_did_upgrade
- an SNS called haoyiran_archive_retrieved
- a lambda function called haoyiran_restore

## workflow
- when user upgrades, /subscribe publishes to SNS haoyiran_a16_did_upgrade
- /thaw receives SNS notification, go poll SQS haoyiran_a16_did_upgrade
- /thaw initiates archive retrieval and register SNS haoyiran_archive_retrieved with Glacier 
- once Glacier finishes retrieval, it publishes to SNS haoyiran_archive_retrieved
- SNS haoyiran_archive_retrived triggers the lambda function
- lambda function 
    a. download file from temporary S3 location
    b. upload file to gas-results bucket
    c. update dynamodb
    d. delete archive file

## benefits
- using lambda function saves us from the cost and effort to maintain a separate server since lambda is serverless and is only running when triggered
- lambda is suitable for performing the restoration task because restoration is not the main functionality of the webside and therefore will only be called once in a while





