'''
Module which configures an account for continuous backup of DynamoDB tables via LambdaStreamsToFirehose. 

Monitors a provided AWS CloudTrail which is forwarded to Amazon CloudWatch Logs, and then uses an AWS Lambda 
function to ensure DynamoDB tables have UpdateStreams configured, that LambdaStreamsToFirehose is deployed 
with the DynamoDB UpateStream as the trigger, and that a Kinesis Firehose Delivery Stream is provided for 
data archive to S3 
'''

import sys

# add the lib directory to the path
sys.path.append('lib')

import os
import boto3
import botocore
import time
import hjson
import re

config = None
regex_pattern = None

version = "1.0.1"

'''
Function that checks if a table should be opted into backups based on a regular expression provided
in the configuration file
'''
def table_regex_optin(dynamo_table_name):
    try:
        if "tableNameMatchRegex" in config:
            global regex_pattern
            if regex_pattern == None:
                regex_pattern = re.compile(config['tableNameMatchRegex'])
                
            # check the regular expression match
            if regex_pattern.match(dynamo_table_name):
                return True
            else:
                return False
        else:
            # no regular expression matching in the configuration
            return True
    except:
        return True

'''
Function reference for how to check whether tables should be backed up - change this
to the specific implementation that you've provided

Spec: boolean = f(string)
'''
optin_function = table_regex_optin

# constants - don't change these!
REGION_KEY = 'AWS_REGION'
LAMBDA_STREAMS_TO_FIREHOSE = "LambdaStreamToFirehose"
LAMBDA_STREAMS_TO_FIREHOSE_VERSION = "1.3.5"
LAMBDA_STREAMS_TO_FIREHOSE_BUCKET = "aws-lambda-streams-to-firehose"
dynamo_client = None
dynamo_resource = None
current_region = None
firehose_client = None
lambda_client = None
    
'''
Initialise the module with the provided or default configuration
'''

def init(config_override):
    global config
    global current_region
    global dynamo_client
    global dynamo_resource
    global firehose_client
    global lambda_client
    
    if config == None:
        # read the configuration file name from the config.loc file
        if config_override == None:
            config_file_name = open('config.loc', 'r').read()
        else:
            config_file_name = config_override
            
        config = hjson.load(open(config_file_name, 'r'))
        print "Loaded configuration from %s" % (config_file_name)

    # load the region from the context
    if current_region == None:
        try:
            current_region = os.environ[REGION_KEY]

            if current_region == None or current_region == '':
                raise KeyError
        except KeyError:
            raise Exception("Unable to resolve environment variable %s" % REGION_KEY)

    # connect to the required services
    if dynamo_client == None:
        dynamo_client = boto3.client('dynamodb', region_name=current_region)
        dynamo_resource = boto3.resource('dynamodb')
        firehose_client = boto3.client('firehose', region_name=current_region)
        lambda_client = boto3.client('lambda', region_name=current_region)
        

'''
Check if a DynamoDB table has update streams enabled, and if not then turn it on
'''
def ensure_stream(table_name):
    table = dynamo_resource.Table(table_name)

    # determine if the table has an update stream
    stream_arn = None
    if table.stream_specification == None or table.stream_specification["StreamEnabled"] == False:
        # enable update streams
        dynamo_client.update_table(
            TableName=table_name,
            StreamSpecification={
                'StreamEnabled': True,
                'StreamViewType': 'NEW_AND_OLD_IMAGES'
            }
        )

        # wait for the table to come out of 'UPDATING' status
        ok = False
        while not ok:
            result = dynamo_client.describe_table(
                TableName=table_name
            )
            if result["Table"]["TableStatus"] == 'ACTIVE':
                ok = True
                print "Enabled Update Stream for %s" % (table_name)

                stream_arn = result["Table"]["LatestStreamArn"]
            else:
                # sleep for 1 second
                time.sleep(1)
    else:
        stream_arn = table.latest_stream_arn

    return stream_arn


'''
Create a new Firehose Delivery Stream
'''
def create_delivery_stream(for_table_name):
    response = firehose_client.create_delivery_stream(
        DeliveryStreamName=for_table_name,
        S3DestinationConfiguration={
            'RoleARN': config['firehoseDeliveryRoleArn'],
            'BucketARN': 'arn:aws:s3:::' + config['firehoseDeliveryBucket'],
            'Prefix': "%s/%s/" % (config['firehoseDeliveryPrefix'], for_table_name),
            'BufferingHints': {
                'SizeInMBs': config['firehoseDeliverySizeMB'],
                'IntervalInSeconds': config['firehoseDeliveryIntervalSeconds']
            },
            'CompressionFormat': 'GZIP'
        }
    )

    print "Created new Firehose Delivery Stream %s" % (response["DeliveryStreamARN"])

    return response["DeliveryStreamARN"]


'''
Check that we have a Firehose Delivery Stream of the same name as the provided DynamoDB Table. If not, then create it
'''
def ensure_firehose_delivery_stream(dynamo_table_name):
    response = None

    try:
        response = firehose_client.describe_delivery_stream(DeliveryStreamName=dynamo_table_name)
    except botocore.exceptions.ClientError as e:
        if e.response['Error']['Code'] == 'ResourceNotFoundException':
            pass

    if response and response["DeliveryStreamDescription"]["DeliveryStreamARN"]:
        delivery_stream_arn = response["DeliveryStreamDescription"]["DeliveryStreamARN"]

        return delivery_stream_arn
    else:
        # delivery stream doesn't exist, so create it
        delivery_stream_arn = create_delivery_stream(dynamo_table_name)

    return delivery_stream_arn


'''
Wire the DynamoDB Update Stream to LambdaStreamsToFirehose, if it isn't already
'''
def ensure_update_stream_event_source(dynamo_stream_arn):
    # ensure that we have a lambda streams to firehose function
    function_arn = ensure_lambda_streams_to_firehose()

    # map the dynamo update stream as a source for this function
    try:
        lambda_client.create_event_source_mapping(
            EventSourceArn=dynamo_stream_arn,
            FunctionName=function_arn,
            Enabled=True,
            BatchSize=config['streamsMaxRecordsBatch'],
            StartingPosition='TRIM_HORIZON'
        )
    except botocore.exceptions.ClientError as e:
        if e.response['Error']['Code'] == 'ResourceConflictException':
            pass
        else:
            raise e


'''
Deploy the LambdaStreamsToFirehose module (https://github.com/awslabs/lambda-streams-to-firehose) if it is not deployed already
'''
def ensure_lambda_streams_to_firehose():
    # make sure we have the LambdaStreamsToFirehose function deployed
    response = None
    try:
        response = lambda_client.get_function(FunctionName=LAMBDA_STREAMS_TO_FIREHOSE)
    except botocore.exceptions.ClientError as e:
        if e.response['Error']['Code'] == 'ResourceNotFoundException':
            pass

    if response and response["Configuration"]["FunctionArn"]:
        function_arn = response["Configuration"]["FunctionArn"]
    else:
        deployment_package = "%s-%s.zip" % (LAMBDA_STREAMS_TO_FIREHOSE, LAMBDA_STREAMS_TO_FIREHOSE_VERSION)
        
        # resolve the bucket based on region
        if current_region == 'us-east-1':
            region_suffix = 'us-std'
        else:
            region_suffix = current_region
            
        deploy_bucket = "%s-%s" % (LAMBDA_STREAMS_TO_FIREHOSE_BUCKET, region_suffix)
        
        print "Deploying %s from s3://%s" % (deployment_package, deploy_bucket)
        response = lambda_client.create_function(
            FunctionName=LAMBDA_STREAMS_TO_FIREHOSE,
            Runtime='nodejs',
            Role=config['lambdaExecRoleArn'],
            Handler='index.handler',
            Code={
                'S3Bucket': deploy_bucket,
                'S3Key': deployment_package
            },
            Description="AWS Lambda Streams to Kinesis Firehose Replicator",
            Timeout=60,
            MemorySize=128,
            Publish=True
        )

        if response:
            function_arn = response["FunctionArn"]
            print "Created New Function %s:%s" % (LAMBDA_STREAMS_TO_FIREHOSE, function_arn)

    return function_arn


'''
Removes a Firehose Delivery Stream, without affecting S3 in any way
'''
def delete_fh_stream(for_table_name):
    try:
        firehose_client.delete_delivery_stream(
            DeliveryStreamName=for_table_name
        )
        
        print "Deleted Firehose Delivery Stream %s" % (for_table_name)
    except botocore.exceptions.ClientError as e:
        if e.response['Error']['Code'] == 'ResourceNotFoundException':
            print "No Firehose Delivery Stream %s Found - OK" % (for_table_name)


'''
Remove the routing of any DynamoDB Update Streams to LambdaStreamsToFirehose
''' 
def remove_stream_trigger(dynamo_table_name):
    # find any update streams that route to Lambda Streams to Firehose and remove them
    event_source_mappings = lambda_client.list_event_source_mappings(FunctionName=LAMBDA_STREAMS_TO_FIREHOSE)
    removed_stream_trigger = False
    
    for mapping in  event_source_mappings['EventSourceMappings']:
        event_source_tokens = mapping['EventSourceArn'].split(":")
        
        # check if this is a dynamo DB event
        event_source_service = event_source_tokens[2]
        
        if event_source_service == 'dynamodb':
            # check if the table matches
            event_source_table = event_source_tokens[5].split("/")[1]
            
            if event_source_table == dynamo_table_name:
                lambda_client.delete_event_source_mapping(UUID=mapping["UUID"])
                
                print "Removed Event Source Mapping for DynamoDB Update Stream %s" % (mapping["EventSourceArn"])

    if not removed_stream_trigger:
        print "No DynamoDB Update Stream Triggers found routing to %s for %s - OK" % (LAMBDA_STREAMS_TO_FIREHOSE, dynamo_table_name)
    
'''
Provision a single table for DynamoDB backup
'''
def configure_table(dynamo_table_name):
    proceed = optin_function(dynamo_table_name)
    
    # ensure that the table has an update stream
    if proceed:                
        dynamo_stream_arn = ensure_stream(dynamo_table_name)
        print "Resolved DynamoDB Stream ARN: %s" % (dynamo_stream_arn)
    
        # now ensure that we have a firehose delivery stream that will route to the backup location
        delivery_stream_arn = ensure_firehose_delivery_stream(dynamo_table_name)
        print "Resolved Firehose Delivery Stream ARN: %s" % (delivery_stream_arn)
    
        # wire the dynamo update stream to the deployed instance of lambda-streams-to-firehose
        ensure_update_stream_event_source(dynamo_stream_arn)
    else:
        print "Not configuring continuous backup for %s as it has been suppressed by the configured Opt-In function" % (dynamo_table_name)


'''
Remove continuous backup via Update Streams, without affecting backup data on S3
'''
def deprovision_table(dynamo_table_name):
    # remote routing of update stream to lambda-streams-to-firehose
    remove_stream_trigger(dynamo_table_name)
    
    # remove the firehose delivery stream
    delete_fh_stream(dynamo_table_name)
