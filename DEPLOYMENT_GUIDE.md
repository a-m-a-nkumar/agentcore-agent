# Lambda Deployment Guide

## Quick Deploy (Recommended)

### Deploy Only Chat Lambda (with latest fixes)

```powershell
.\DEPLOY_CHAT_LAMBDA.ps1
```

This will:
1. ✅ Verify AWS credentials
2. ✅ Package the `lambda_chat_package` directory
3. ✅ Deploy to `brd_chat_lambda` function

**Time:** ~30 seconds

---

## Deploy All Lambda Functions

If you need to deploy all Lambda functions:

```powershell
.\deploy_updated_lambdas.ps1
```

This deploys:
- `brd_generator_lambda`
- `brd_chat_lambda`

---

## Manual Deployment Steps

If you prefer to deploy manually:

### Step 1: Package the Lambda

```powershell
# Navigate to the package directory
cd lambda_chat_package

# Create zip file (exclude cache files)
Compress-Archive -Path * -Exclude "__pycache__","*.pyc" -DestinationPath ../lambda_chat_package.zip -Force

# Return to root
cd ..
```

### Step 2: Deploy to AWS

```powershell
aws lambda update-function-code `
    --function-name brd_chat_lambda `
    --zip-file fileb://lambda_chat_package.zip `
    --region us-east-1
```

### Step 3: Verify Deployment

```powershell
aws lambda get-function `
    --function-name brd_chat_lambda `
    --region us-east-1 `
    --query 'Configuration.LastModified'
```

---

## Prerequisites

### 1. AWS CLI Installed

```powershell
# Check if AWS CLI is installed
aws --version

# If not installed, download from:
# https://aws.amazon.com/cli/
```

### 2. AWS Credentials Configured

```powershell
# Configure AWS credentials
aws configure

# Or verify existing credentials
aws sts get-caller-identity
```

**Required:**
- AWS Access Key ID
- AWS Secret Access Key
- Default region: `us-east-1`

### 3. Lambda Function Exists

The Lambda function `brd_chat_lambda` must already exist in your AWS account.

**To check:**
```powershell
aws lambda get-function --function-name brd_chat_lambda --region us-east-1
```

**If it doesn't exist**, you'll need to create it first (see "Creating Lambda Function" below).

---

## Troubleshooting

### Error: "Function not found"

**Problem:** The Lambda function doesn't exist.

**Solution:** Create the function first (see below) or check the function name.

### Error: "Access Denied"

**Problem:** Your AWS credentials don't have permission to update Lambda functions.

**Solution:** Ensure your IAM user/role has `lambda:UpdateFunctionCode` permission.

**Required IAM Policy:**
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "lambda:UpdateFunctionCode",
                "lambda:GetFunction"
            ],
            "Resource": "arn:aws:lambda:us-east-1:*:function:brd_chat_lambda"
        }
    ]
}
```

### Error: "Package too large"

**Problem:** The zip file exceeds Lambda's 50MB limit (250MB unzipped).

**Solution:** 
- Remove unnecessary files from `lambda_chat_package/`
- Use Lambda Layers for large dependencies
- Check for duplicate packages

### Error: "Timeout during upload"

**Problem:** Network issues or very large package.

**Solution:**
- Check internet connection
- Try uploading via S3 (see "Deploy via S3" below)

---

## Deploy via S3 (For Large Packages)

If the package is too large for direct upload:

### Step 1: Upload to S3

```powershell
aws s3 cp lambda_chat_package.zip s3://your-bucket-name/lambda_chat_package.zip
```

### Step 2: Deploy from S3

```powershell
aws lambda update-function-code `
    --function-name brd_chat_lambda `
    --s3-bucket your-bucket-name `
    --s3-key lambda_chat_package.zip `
    --region us-east-1
```

---

## Creating Lambda Function (If It Doesn't Exist)

If the Lambda function doesn't exist, create it first:

### Step 1: Create IAM Role

```powershell
# Create trust policy
@"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
"@ | Out-File -FilePath trust-policy.json

# Create role
aws iam create-role `
    --role-name brd-chat-lambda-role `
    --assume-role-policy-document file://trust-policy.json

# Attach basic execution policy
aws iam attach-role-policy `
    --role-name brd-chat-lambda-role `
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
```

### Step 2: Create Lambda Function

```powershell
aws lambda create-function `
    --function-name brd_chat_lambda `
    --runtime python3.13 `
    --role arn:aws:iam::YOUR_ACCOUNT_ID:role/brd-chat-lambda-role `
    --handler lambda_brd_chat.lambda_handler `
    --zip-file fileb://lambda_chat_package.zip `
    --timeout 300 `
    --memory-size 512 `
    --region us-east-1 `
    --environment "Variables={
        BEDROCK_MODEL_ID=global.anthropic.claude-sonnet-4-5-20250929-v1:0,
        BEDROCK_REGION=us-east-1,
        S3_BUCKET_NAME=test-development-bucket-siriusai,
        AGENTCORE_MEMORY_ID=Test-DGwqpP7Rvj,
        AGENTCORE_ACTOR_ID=brd-session
    }"
```

**Replace:**
- `YOUR_ACCOUNT_ID` with your AWS account ID
- Environment variables with your actual values

---

## Verification

After deployment, verify it's working:

### 1. Check Function Status

```powershell
aws lambda get-function --function-name brd_chat_lambda --region us-east-1
```

### 2. Test with a Simple Invocation

```powershell
aws lambda invoke `
    --function-name brd_chat_lambda `
    --region us-east-1 `
    --payload '{"action":"get_history","session_id":"test"}' `
    response.json

cat response.json
```

### 3. Check CloudWatch Logs

```powershell
aws logs tail /aws/lambda/brd_chat_lambda --follow --region us-east-1
```

---

## What Changed in This Update

The latest update to `lambda_brd_chat.py` includes:

1. **Section Title Matching**
   - Now supports: "update sarah to aman in section stakeholders"
   - Previously only worked with section numbers

2. **S3 Save Verification**
   - Verifies that BRD saves successfully
   - Reloads BRD from S3 after updates to ensure fresh data

3. **Better Error Handling**
   - More detailed logging
   - Clearer error messages

4. **Fresh Data Loading**
   - Always loads BRD fresh from S3 before processing
   - Reloads after updates to prevent stale data

---

## Next Steps After Deployment

1. **Test the Fix:**
   ```
   Try: "update sarah chen to aman in section stakeholders"
   Then: "show stakeholders" to verify
   ```

2. **Monitor Logs:**
   ```powershell
   aws logs tail /aws/lambda/brd_chat_lambda --follow
   ```

3. **Verify Updates Persist:**
   - Make an update
   - Wait a few seconds
   - Request the section again
   - Verify the change is still there

---

## Support

If you encounter issues:

1. Check CloudWatch Logs for errors
2. Verify AWS credentials and permissions
3. Ensure the Lambda function exists
4. Check that the zip file was created correctly

For more help, see the main project README or check AWS Lambda documentation.




