import boto3
import time

log_group = "/aws/bedrock-agentcore/runtimes/my_agent-0BLwDgF9uK-DEFAULT"
region = "us-east-1"

client = boto3.client('logs', region_name=region)

try:
    # Get latest stream
    streams = client.describe_log_streams(
        logGroupName=log_group,
        orderBy='LastEventTime',
        descending=True,
        limit=1
    )
    
    if not streams['logStreams']:
        print("No log streams found.")
    else:
        stream_name = streams['logStreams'][0]['logStreamName']
        print(f"Reading from stream: {stream_name}")
        
        events = client.get_log_events(
            logGroupName=log_group,
            logStreamName=stream_name,
            limit=20,
            startFromHead=False
        )
        
        with open("latest_logs.txt", "w", encoding="utf-8") as f:
            for event in events['events']:
                f.write(f"{event['timestamp']} - {event['message']}\n")
        print("Logs written to latest_logs.txt")

except Exception as e:
    print(f"Error: {e}")
