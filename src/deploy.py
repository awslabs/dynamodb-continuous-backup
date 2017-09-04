#!/usr/bin/env python

import sys

# add the lib directory to the path
sys.path.append('lib')

import boto3
import botocore
import argparse
import shortuuid
import hjson
import json

cwe_client = None
lambda_client = None
version = '1.5'
LAMBDA_FUNCTION_NAME = 'EnsureDynamoBackup'
DDB_CREATE_DELETE_RULE_NAME = 'DynamoDBCreateDelete'


def configure_cwe(region, cwe_role_arn):
    # connect to CloudWatch Logs
    global cwe_client
    cwe_client = boto3.client('events', region_name=region)

    # determine if there's an existing rule in place
    rule_query_response = {}
    try:
        rule_query_response = cwe_client.describe_rule(
                Name=DDB_CREATE_DELETE_RULE_NAME
        )
    except botocore.exceptions.ClientError as e:
        code = e.response['Error']['Code']
        if code == 'ResourceNotFoundException':
            pass
        else:
            raise e

    if 'Arn' in rule_query_response:
        print "Resolved existing DynamoDB CloudWatch Event Subscriber Rule %s" % (rule_query_response['Arn']);

        return rule_query_response['Arn']
    else:
        # create a cloudwatch events rule
        rule_response = cwe_client.put_rule(
            Name=DDB_CREATE_DELETE_RULE_NAME,
            EventPattern='{"detail-type":["AWS API Call via CloudTrail"],"detail":{"eventSource":["dynamodb.amazonaws.com"],"eventName":["DeleteTable","CreateTable"]}}',
            State='ENABLED',
            Description='CloudWatch Events Rule to React to DynamoDB Create and DeleteTable events',
            RoleArn=cwe_role_arn
        )

        print "Created new CloudWatch Events Rule %s" % (rule_response["RuleArn"])
        return rule_response["RuleArn"]


def deploy_lambda_function(region, lambda_role_arn, cwe_rule_arn, force):
    # connect to lambda
    global lambda_client
    lambda_client = boto3.client('lambda', region_name=region)

    deployment_zip = open('../dist/dynamodb_continuous_backup-%s.zip' % (version), 'rb')
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

                print "Redeployed DynamoDB Ensure Backup Module to %s" % (response['FunctionArn'])
            else:
                response = lambda_client.get_function(
                                FunctionName=LAMBDA_FUNCTION_NAME
                            )
                function_arn = response['Configuration']['FunctionArn']

                print "Using existing DynamoDB Ensure Backup Module at %s" % (function_arn)
        else:
            raise e

    # query for a permission being granted to the function
    policy = None
    response_doc = None
    events_grant_ok = False
    try:
        policy = lambda_client.get_policy(FunctionName=LAMBDA_FUNCTION_NAME)['Policy']
        response_doc = json.loads(policy)

        if 'Statement' in response_doc:
            # spin through and determine if an Allow grant has been made to CW Events to InvokeFunction
            for x in response_doc['Statement']:
                if 'Action' in x and x['Action'] == 'lambda:InvokeFunction' and x['Effect'] == 'Allow' and x['Principal']['Service'] == 'events.amazonaws.com':
                    events_grant_ok = True
                    break;
    except botocore.exceptions.ClientError as e:
        code = e.response['Error']['Code']
        if code == 'ResourceNotFoundException':
            pass
        else:
            print e

    if events_grant_ok:
        print "Permission to execute Lambda function already granted to CloudWatch Events"
    else:
        # add a permission for CW Events to invoke this function
        response = lambda_client.add_permission(
            FunctionName=LAMBDA_FUNCTION_NAME,
            StatementId=shortuuid.uuid(),
            Action='lambda:InvokeFunction',
            Principal='events.amazonaws.com',
            SourceArn=cwe_rule_arn
        )

        print "Granted permission to execute Lambda function to CloudWatch Events"

    return function_arn


def create_lambda_cwe_target(lambda_arn):
    existing_targets = cwe_client.list_targets_by_rule(
        Rule=DDB_CREATE_DELETE_RULE_NAME
    )

    if 'Targets' not in existing_targets or len(existing_targets['Targets']) == 0:
        cwe_client.put_targets(
            Rule=DDB_CREATE_DELETE_RULE_NAME,
            Targets=[
                {
                    'Id': shortuuid.uuid(),
                    'Arn': lambda_arn
                }
            ]
        )

        print "Created CloudWatchEvents Target for Rule %s" % (DDB_CREATE_DELETE_RULE_NAME)
    else:
        print "Existing CloudWatchEvents Rule has correct Target Function"


def configure_backup(region, cwe_role_arn, lambda_role_arn, redeploy_lambda):
    # setup a CloudWatchEvents Rule
    cwe_rule_arn = configure_cwe(region, cwe_role_arn)

    # deploy the lambda function
    lambda_arn = deploy_lambda_function(region, lambda_role_arn, cwe_rule_arn, redeploy_lambda)

    # create a target for our CloudWatch Events Rule that points to the Lambda function
    create_lambda_cwe_target(lambda_arn)



if __name__ == "__main__":
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)    
    parser.add_argument("--config-file", dest='config_file', action='store', required=False, help="Enter the path to the JSON or HJSON configuration file")
    parser.add_argument("--region", dest='region', action='store', required=False, help="Enter the destination region")
    parser.add_argument("--cw_role_arn", dest='cw_role_arn', action='store', required=False, help="The CloudWatch Events Role ARN")
    parser.add_argument("--lambda_role_arn", dest='lambda_role_arn', action='store', required=False, help="The Lambda Execution Role ARN")
    parser.add_argument("--redeploy", dest='redeploy', action='store_true', required=False, help="Redeploy the Lambda function?")
    args = parser.parse_args()

    if args.config_file != None:
        # load the configuration file
        config = hjson.load(open(args.config_file, 'r'))

        configure_backup(config['region'], config['cloudWatchRoleArn'], config['lambdaExecRoleArn'], args.redeploy)
    else:
        # no configuration file provided so we need region, CW Role and Lambda Exec role args
        if args.region == None or args.cw_role_arn == None or args.lambda_role_arn == None:
            parser.print_help()
        else:
            configure_backup(args.region, args.cw_role_arn, args.lambda_role_arn, args.redeploy)
