#!/usr/bin/env python3
"""
Create Lambda deployment zip file properly, preserving directory structure.
This script ensures that jmespath/ast.py stays in jmespath/ and doesn't shadow Python's built-in ast module.
"""

import os
import zipfile
import sys
from pathlib import Path

def create_lambda_zip(package_dir, output_zip):
    """Create a zip file from the package directory, preserving structure."""
    package_path = Path(package_dir)
    output_path = Path(output_zip)
    
    # Remove old zip if exists
    if output_path.exists():
        output_path.unlink()
        print(f"  Removed old {output_zip}")
    
    # Files/directories to exclude
    exclude_patterns = [
        '__pycache__',
        '*.pyc',
        '.DS_Store',
        '*.pyc',
        '.git',
        '.gitignore',
    ]
    
    # Files to explicitly exclude (should not be at root)
    exclude_files = ['ast.py']  # Never include ast.py at root level
    
    print(f"  Creating zip from {package_dir}...")
    
    file_count = 0
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        # Walk through all files in the package directory
        for root, dirs, files in os.walk(package_path):
            # Skip __pycache__ directories
            dirs[:] = [d for d in dirs if d != '__pycache__']
            
            for file in files:
                # Skip .pyc files
                if file.endswith('.pyc'):
                    continue
                
                # Skip .DS_Store
                if file == '.DS_Store':
                    continue
                
                # Skip ast.py at root level (it would shadow Python's built-in ast)
                file_path = Path(root) / file
                relative_path = file_path.relative_to(package_path)
                
                # Check if this is ast.py at root level
                if file == 'ast.py' and len(relative_path.parts) == 1:
                    print(f"  [WARNING] Skipping root-level ast.py: {relative_path}")
                    continue
                
                # Add file to zip with proper path
                arcname = str(relative_path).replace('\\', '/')  # Use forward slashes for zip
                zipf.write(file_path, arcname)
                file_count += 1
    
    zip_size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"  [OK] Package created: {output_zip} ({zip_size_mb:.2f} MB, {file_count} files)")
    
    # Verify no ast.py at root level
    with zipfile.ZipFile(output_path, 'r') as zipf:
        root_ast_files = [name for name in zipf.namelist() 
                         if name == 'ast.py' or name.startswith('ast.py/')]
        if root_ast_files:
            print(f"  [ERROR] Found ast.py at root level in zip: {root_ast_files}")
            sys.exit(1)
    
    print(f"  [OK] Verified: No ast.py at root level")
    return True

if __name__ == '__main__':
    package_dir = 'lambda_chat_package'
    output_zip = 'lambda_chat_package.zip'
    
    if not os.path.exists(package_dir):
        print(f"Error: {package_dir} directory not found!")
        sys.exit(1)
    
    create_lambda_zip(package_dir, output_zip)


