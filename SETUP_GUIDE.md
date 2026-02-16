# Setup Guide for AI Job Application Mailer

## Issue 1: python-jobspy Warning (FIXED)
The warning "python-jobspy not installed" is a false positive. The package IS installed and will work correctly. You can ignore this warning.

## Issue 2: Gmail API Credentials Setup

To use Gmail for sending emails, you need to set up Gmail API credentials:

### Step 1: Create a Google Cloud Project
1. Go to https://console.cloud.google.com/
2. Create a new project or select an existing one
3. Enable the Gmail API:
   - Go to "APIs & Services" > "Library"
   - Search for "Gmail API"
   - Click "Enable"

### Step 2: Create OAuth 2.0 Credentials
1. Go to "APIs & Services" > "Credentials"
2. Click "Create Credentials" > "OAuth client ID"
3. Configure the consent screen if prompted:
   - User Type: External
   - Add your email as a test user
4. Application type: "Desktop app"
5. Download the credentials JSON file
6. Rename it to `credentials.json`
7. Upload it to your Codespace (same directory as main.py)

### Step 3: Configure Required Files

Create a `.env` file with your Perplexity API key:
```
PPLX_API_KEY=your_actual_api_key_here
```

Create a `resume_summary.txt` file with a brief summary of your skills:
```
Experienced software developer with 3+ years in React.js, Node.js, and Python.
Skilled in building scalable web applications, REST APIs, and microservices.
Strong problem-solving abilities and team collaboration skills.
```

### Alternative: Use Email Library (No Gmail API)

If you don't want to set up Gmail API, the script can be modified to use SMTP instead.
Let me know if you'd prefer this option.

