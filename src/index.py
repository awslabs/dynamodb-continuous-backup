'''
AWS Lambda function which receives CloudTrail events via CloudWatch Logging and implements DynamoDB Continuous Backups
'''

import sys

# add the lib directory to the path
sys.path.append('lib')

import json
import base64
import zlib
import dynamo_continuous_backup as backup

config = None

def event_handler(event, context):
    # initialise the ddb continuous backup manager
    backup.init(None)

    # handle unknown event types
    if event["awslogs"]["data"] == None:
        print "Unknown input event type"
    else:
        # extract CloudTrail Event
        decoded = base64.decodestring(event["awslogs"]["data"])
        # decompress the data shipped as base64 gzip
        decompressed = zlib.decompress(decoded, 15 + 32)
        cloudtrail_payload = json.loads(decompressed)
        eventCount = 0

        # may receive multiple CloudTrail events
        for logEvent in cloudtrail_payload["logEvents"]:
            eventCount += 1
            event = json.loads(logEvent["message"])

            if event["eventSource"] == "dynamodb.amazonaws.com" and event['eventName'] == "CreateTable" and (
                    'errorCode' not in event or event['errorCode'] == None):
                # resolve the table
                dynamo_table_name = event["requestParameters"]["tableName"]

                # configure the table for continuous backup
                backup.configure_table(dynamo_table_name)
            elif event["eventSource"] == "dynamodb.amazonaws.com" and event['eventName'] == "DeleteTable" and (
                    'errorCode' not in event or event['errorCode'] == None):
                # delete the firehose delivery stream for this table
                dynamo_table_name = event["requestParameters"]["tableName"]
                
                # deprovision table for continuous backup
                backup.deprovision_table(dynamo_table_name)
            else:
                print "Unknown Event %s:%s" % (event["eventSource"], event['eventName'])
                print event

        print "Processed %s Events" % (eventCount)