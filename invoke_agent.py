import json
import uuid
import boto3

agent_arn = "arn:aws:bedrock-agentcore:us-east-1:448049797912:runtime/my_agent-0BLwDgF9uK"
prompt = "Tell me a joke"

# Initialize the Amazon Bedrock AgentCore client
# Explicitly setting region to us-east-1 as that's where the agent is deployed
agent_core_client = boto3.client('bedrock-agentcore', region_name='us-east-1')

# Prepare the payload
payload = json.dumps({"prompt": prompt}).encode()

print(f"Invoking agent: {agent_arn}")
try:
    # Invoke the agent
    response = agent_core_client.invoke_agent_runtime(
        agentRuntimeArn=agent_arn,
        runtimeSessionId=str(uuid.uuid4()),
        payload=payload,
        qualifier="DEFAULT"
    )

    content = []
    for chunk in response.get("response", []):
        content.append(chunk.decode('utf-8'))

    print("Response:")
    print(json.loads(''.join(content)))

except Exception as e:
    print(f"Error invoking agent: {e}")
