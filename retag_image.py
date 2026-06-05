#!/usr/bin/env python3
"""
ECR Image Retagging Utility
Retags Docker images in AWS ECR from 'latest' to 'agentcore'
"""
import boto3
import sys
import os
from datetime import datetime

# Configuration - Can be overridden by environment variables
REPO_NAME = os.getenv('ECR_REPOSITORY', 'deluxe-sdlc')
REGION = os.getenv('AWS_REGION', 'us-east-1')
SOURCE_TAG = os.getenv('SOURCE_TAG', 'latest')
TARGET_TAG = os.getenv('TARGET_TAG', 'agentcore')

def print_header():
    """Print script header"""
    print("=" * 60)
    print("🏷️  ECR Image Retagging Utility")
    print("=" * 60)
    print(f"Repository: {REPO_NAME}")
    print(f"Region: {REGION}")
    print(f"Source Tag: {SOURCE_TAG}")
    print(f"Target Tag: {TARGET_TAG}")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    print()

def retag_image():
    """Main retagging function"""
    try:
        client = boto3.client('ecr', region_name=REGION)
        
        print(f"🔍 Looking for image with tag '{SOURCE_TAG}'...")
        
        # Get manifest
        response = client.batch_get_image(
            repositoryName=REPO_NAME,
            imageIds=[{'imageTag': SOURCE_TAG}]
        )

        if not response.get('images'):
            print(f"❌ Error: Image with tag '{SOURCE_TAG}' not found in repository '{REPO_NAME}'")
            print(f"\n💡 Tip: Verify the image exists:")
            print(f"   aws ecr describe-images --repository-name {REPO_NAME} --region {REGION}")
            sys.exit(1)

        image = response['images'][0]
        manifest = image['imageManifest']
        
        print(f"✅ Found image with tag '{SOURCE_TAG}'")
        print(f"   Image digest: {image.get('imageId', {}).get('imageDigest', 'N/A')[:20]}...")
        
        # Check if target tag already exists
        print(f"\n🔍 Checking if tag '{TARGET_TAG}' already exists...")
        try:
            existing = client.batch_get_image(
                repositoryName=REPO_NAME,
                imageIds=[{'imageTag': TARGET_TAG}]
            )
            if existing.get('images'):
                print(f"⚠️  Tag '{TARGET_TAG}' already exists - it will be overwritten")
        except Exception:
            print(f"✅ Tag '{TARGET_TAG}' does not exist yet")

        # Put image with new tag
        print(f"\n🏷️  Applying tag '{TARGET_TAG}'...")
        client.put_image(
            repositoryName=REPO_NAME,
            imageTag=TARGET_TAG,
            imageManifest=manifest
        )

        print(f"✅ Successfully retagged image to '{TARGET_TAG}'")
        
        # Verify the tag was created
        print(f"\n🔍 Verifying tag creation...")
        verify_response = client.batch_get_image(
            repositoryName=REPO_NAME,
            imageIds=[{'imageTag': TARGET_TAG}]
        )
        
        if verify_response.get('images'):
            print(f"✅ Verification successful - tag '{TARGET_TAG}' exists")
            
            # Remove 'latest' tag if it exists (to avoid conflicts)
            if SOURCE_TAG == 'latest' and TARGET_TAG != 'latest':
                print(f"\n🗑️  Removing 'latest' tag to avoid conflicts...")
                try:
                    # Delete only the 'latest' tag (not the image itself)
                    # Using imageTag removes just that tag reference
                    client.batch_delete_image(
                        repositoryName=REPO_NAME,
                        imageIds=[{'imageTag': 'latest'}]
                    )
                    print(f"✅ Removed 'latest' tag (image now only tagged as '{TARGET_TAG}')")
                except client.exceptions.ImageNotFoundException:
                    print(f"ℹ️  'latest' tag not found (may have been already removed)")
                except Exception as e:
                    print(f"⚠️  Could not remove 'latest' tag: {e}")
                    print(f"   (This is not critical - image is still tagged as '{TARGET_TAG}')")
            else:
                print(f"\nℹ️  Skipping 'latest' tag removal (source: '{SOURCE_TAG}', target: '{TARGET_TAG}')")
            
            # List all tags for this image
            print(f"\n📋 All tags for this image:")
            try:
                image_detail = client.describe_images(
                    repositoryName=REPO_NAME,
                    imageIds=[{'imageTag': TARGET_TAG}]
                )
                tags = image_detail['imageDetails'][0].get('imageTags', [])
                for tag in sorted(tags):
                    print(f"   • {tag}")
            except Exception as e:
                print(f"   ⚠️  Could not list all tags: {e}")
        else:
            print(f"⚠️  Warning: Could not verify tag creation")
        
        print("\n" + "=" * 60)
        print("🎉 Retagging completed successfully!")
        print("=" * 60)
        print(f"\n💡 Update your agent configuration to use:")
        print(f"   {REPO_NAME}:{TARGET_TAG}")
        print()
        
        return 0

    except client.exceptions.RepositoryNotFoundException:
        print(f"❌ Error: Repository '{REPO_NAME}' not found in region '{REGION}'")
        print(f"\n💡 Tip: List available repositories:")
        print(f"   aws ecr describe-repositories --region {REGION}")
        return 1
    
    except client.exceptions.ImageNotFoundException:
        print(f"❌ Error: Image with tag '{SOURCE_TAG}' not found")
        return 1
    
    except Exception as e:
        print(f"❌ Error: {e}")
        print(f"\n💡 Debug info:")
        print(f"   Repository: {REPO_NAME}")
        print(f"   Region: {REGION}")
        print(f"   Source Tag: {SOURCE_TAG}")
        print(f"   Target Tag: {TARGET_TAG}")
        return 1

if __name__ == "__main__":
    print_header()
    exit_code = retag_image()
    sys.exit(exit_code)
