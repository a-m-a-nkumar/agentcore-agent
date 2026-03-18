import os
import logging
import json

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    logger.info('## ENVIRONMENT VARIABLES\r' + str(dict(**os.environ)))
    logger.info('## EVENT\r' + str(event))
    logger.info('## CONTEXT\r' + str(context))

    # Check if Records exist
    if 'Records' not in event or not event['Records']:
        raise ValueError("No records found in the event")

    # Extract information from the SQS event
    message_body = event['Records'][0]['body']
    
    return {
        'message': message_body,
        'FunctionCount': 1  # Dummy value for demonstration
    }

def main(event_file_path=None):
    """
    Run lambda_handler with a local event file.
    If event_file_path is None, defaults to '../event.json' relative to this file.
    """
    if event_file_path is None:
        event_file_path = os.path.join(os.path.dirname(__file__), '../event.json')

    # Load the event
    with open(event_file_path, 'r') as file:
        event = json.load(file)

    # Example context for testing
    context = {'requestid': '1234'}

    result = lambda_handler(event, context)
    print("Result:", result)

if __name__ == "__main__":
    main()
