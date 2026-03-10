"""
Quick script to add Atlassian columns through the API/backend context
Run this with: python -c "import add_atlassian_columns_quick; add_atlassian_columns_quick.run()"
"""
import sys
import os

# Ensure we're in the right directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Now run the migration
from migrations.add_atlassian_credentials import add_atlassian_columns

if __name__ == "__main__":
    add_atlassian_columns()
