import sys
import os
import unittest
import logging
import json

# Add function folder to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../function')))

# Import directly
from lambda_function import lambda_handler, main

# Setup logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Event file path
EVENT_FILE = os.path.join(os.path.dirname(__file__), '../event.json')

class TestFunction(unittest.TestCase):

    def setUp(self):
        # Load event.json
        with open(EVENT_FILE, 'r') as file:
            self.event = json.load(file)
        # Dummy context
        self.context = {'requestid': '1234'}

    def test_lambda_handler(self):
        logger.info('## EVENT')
        logger.info(json.dumps(self.event, indent=2))
        result = lambda_handler(self.event, self.context)
        self.assertIn('FunctionCount', result)
        self.assertEqual(result.get('message'), "Hello from SQS!")

    def test_lambda_handler_with_empty_body(self):
        self.event['Records'][0]['body'] = ""
        result = lambda_handler(self.event, self.context)
        self.assertEqual(result.get('message'), "", "Message should be empty")
        self.assertIn('FunctionCount', result)

    def test_lambda_handler_with_missing_body(self):
        del self.event['Records'][0]['body']
        with self.assertRaises(KeyError):
            lambda_handler(self.event, self.context)

    def test_lambda_handler_with_no_records(self):
        self.event['Records'] = []
        with self.assertRaises(ValueError) as context:
            lambda_handler(self.event, self.context)
        self.assertEqual(str(context.exception), "No records found in the event")

    def test_main(self):
        main()

if __name__ == '__main__':
    unittest.main()
