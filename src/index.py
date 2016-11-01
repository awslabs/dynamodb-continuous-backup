'''
AWS Lambda function which receives CloudTrail events via CloudWatch Logging and implements DynamoDB Continuous Backups
'''

import sys

# add the lib directory to the path
sys.path.append('lib')

import dynamo_continuous_backup as backup

config = None

def event_handler(event, context):
    # initialise the ddb continuous backup manager
    backup.init(None)
    
    # handle unknown event types
    if 'detail' not in event or 'requestParameters' not in event["detail"] or event['detail']['eventSource'] != 'dynamodb.amazonaws.com':
        print "Unknown input event type"
        print event
    else:
        if event['detail']['eventName'] == "CreateTable":
            # resolve the table
            dynamo_table_name = event["detail"]["requestParameters"]["tableName"]

            # configure the table for continuous backup
            backup.configure_table(dynamo_table_name)
        elif event['detail']['eventName'] == "DeleteTable":
            # delete the firehose delivery stream for this table
            dynamo_table_name = event["detail"]["requestParameters"]["tableName"]
            
            # deprovision table for continuous backup
            backup.deprovision_table(dynamo_table_name)
        else:
            print "Unknown Event %s" % (event['detail']['eventName'])
            print event