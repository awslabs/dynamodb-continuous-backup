#!/usr/bin/env python

import sys

# add the lib directory to the path
sys.path.append('lib')

import boto3
import botocore
import argparse
import shortuuid
import hjson

cwl_client = None
ct_client = None
lambda_client = None

LAMBDA_FUNCTION_NAME = 'EnsureDynamoBackup'

def get_log_group_name(for_trail):
    return "CloudTrail/" + for_trail


def setup_cwl(region, use_trail):
    # connect to CloudWatch Logs
    global cwl_client
    cwl_client = boto3.client('logs', region_name=region)
    
    log_group_name = get_log_group_name(use_trail)
    
    # setup the cloudwatch log group for CloudTrail
    created = False
    try:
        cwl_client.create_log_group(logGroupName=log_group_name)
        
        created = True
    except botocore.exceptions.ClientError as e:
        if e.response['Error']['Code'] == 'ResourceAlreadyExistsException':
            pass
    
    response = cwl_client.describe_log_groups(logGroupNamePrefix=log_group_name)
    
    cwl_group_arn = response['logGroups'][0]['arn']    
    
    if created:
        print "Created new CloudWatch Log Group %s" % (cwl_group_arn)
        
    return cwl_group_arn


def setup_trail(region, use_trail, cwl_group_arn, cw_logs_role_arn):
    # connect to cloudtrail
    global ct_client
    ct_client = boto3.client('cloudtrail', region_name=region)

    # forward the provided CloudTrail to the newly created log group
    ct_client.update_trail(
        Name=use_trail,
        CloudWatchLogsLogGroupArn=cwl_group_arn,
        CloudWatchLogsRoleArn=cw_logs_role_arn
    )
    
    print "Linked CloudTrail %s to CloudWatch Logs Group %s" % (use_trail, cwl_group_arn)
        

def deploy_lambda_function(region, lambda_role_arn, cwl_group_arn, force):
    # connect to lambda
    global lambda_client
    lambda_client = boto3.client('lambda', region_name=region)
    
    deployment_zip = open('../dist/dynamodb_continuous_backup.zip', 'rb')
    deployment_contents = deployment_zip.read()
    deployment_zip.close()
    
    response = None
    function_arn = None  
    try:
        response = lambda_client.create_function(
                        FunctionName=LAMBDA_FUNCTION_NAME,
                        Runtime='python2.7',
                        Role=lambda_role_arn,
                        Handler='index.event_handler',
                        Code={
                            'ZipFile': deployment_contents,
                        },
                        Description="Function to ensure DynamoDB tables are configured with continuous backup",
                        Timeout=300,
                        MemorySize=128,
                        Publish=True
                    )
        function_arn = response['FunctionArn']
        
        print "Deployed new DynamoDB Ensure Backup Module to %s" % (function_arn)
    except botocore.exceptions.ClientError as e:
        code = e.response['Error']['Code']
        if code == 'ResourceAlreadyExistsException' or code == 'ResourceConflictException':
            if force:
                response = lambda_client.update_function_code(
                    FunctionName=LAMBDA_FUNCTION_NAME,
                    ZipFile=deployment_contents,
                    Publish=True
                )                                
                function_arn = response['FunctionArn']
                # store the arn with the version number stripped off
                function_arn = ":".join(function_arn.split(":")[:7])
                
                print "Redeployed DynamoDB Ensure Backup Module to %s" % (function_arn)
            else:
                response = lambda_client.get_function(
                                FunctionName=LAMBDA_FUNCTION_NAME
                            )
                function_arn = response['Configuration']['FunctionArn']
                
                print "Using existing DynamoDB Ensure Backup Module at %s" % (function_arn)
        else:
            raise e
        
    # add a permission for CW Logs to invoke this function
    response = lambda_client.add_permission(
        FunctionName=LAMBDA_FUNCTION_NAME,
        StatementId=shortuuid.uuid(),
        Action='lambda:InvokeFunction',
        Principal='logs.%s.amazonaws.com' % (region),
        SourceArn=cwl_group_arn
    )
    
    print "Granted permission to execute backup functions to CloudWatch Logging"
    
    return function_arn
    

def create_lambda_cwl_subscription(lambda_arn, cw_logs_role_arn, use_trail):
    # create a log group filter for the subscription that only delivers dynamoDB events
    log_group_name = get_log_group_name(use_trail);

    print lambda_arn
    
    cwl_client.put_subscription_filter(
        logGroupName=log_group_name,
        filterName='DynamoDB Events Only',
        filterPattern='{ $.eventSource = "dynamodb.amazonaws.com" && ($.eventName = "CreateTable" || $.eventName = "DeleteTable") }',
        destinationArn=lambda_arn
    )
    
    print "Created Subscription Filter for DynamoDB only events on CloudTrail Log Stream"



def configure_ct(region, cw_logs_role_arn, lambda_role_arn, use_trail, redeploy_lambda):
    # setup a CloudWatch Logs Group
    cwl_group_arn = setup_cwl(region, use_trail)
    
    # configure CloudTrail with forwarding the trail to the CWLogGroup
    setup_trail(region, use_trail, cwl_group_arn, cw_logs_role_arn)
    
    # deploy the lambda function
    lambda_arn = deploy_lambda_function(region, lambda_role_arn, cwl_group_arn, redeploy_lambda)
    
    # create a lambda subscription to the cwlogs group with a filter for DDB CreateTable events
    create_lambda_cwl_subscription(lambda_arn, cw_logs_role_arn, use_trail)



if __name__ == "__main__":
    parser = argparse.ArgumentParser()    
    parser.add_argument("--config-file", dest='config_file', action='store', required=True, help="Enter the path to the JSON or HJSON configuration file")
    parser.add_argument("--redeploy", dest='redeploy', action='store_true', required=False, help="Redeploy the Lambda function?")    
    args = parser.parse_args()
    
    # load the configuration file
    config = hjson.load(open(args.config_file, 'r'))
    
    # invoke the cloudtrail configuration module
    configure_ct(config['region'], config['cloudTrailRoleArn'], config['lambdaExecRoleArn'], config['cloudTrailName'], args.redeploy)