#!/usr/bin/env python3
"""
AI Job Application Mailer
Automated job application system that scrapes job postings, finds HR contacts,
and sends personalized AI-generated emails with resume attachments.

Dependencies:
    pip install python-jobspy perplexity-python google-api-python-client google-auth-oauthlib \
                google-auth-httplib2 pandas python-dotenv requests beautifulsoup4
"""

import os
import sys
import json
import time
import base64
import re
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime, timedelta
import pandas as pd
from dotenv import load_dotenv
import pickle

# Gmail API imports
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Job scraping
try:
    from python_jobspy import scrape_jobs
except ImportError:
    print("Warning: python-jobspy not installed. Job scraping will not work.")
    scrape_jobs = None

# Perplexity API
try:
    from openai import OpenAI
except ImportError:
    print("Warning: openai library not installed. Using requests for Perplexity API.")
    import requests

# Configuration
load_dotenv()

SCOPES = ['https://www.googleapis.com/auth/gmail.send']
CONFIG = {
    'PPLX_API_KEY': os.getenv('PPLX_API_KEY', ''),
    'PPLX_MODEL': 'llama-3.1-sonar-large-128k-online',
    'RECIPIENTS_CSV': 'recipients.csv',
    'BLOCKED_DOMAINS_FILE': 'blocked_domains.json',
    'CREDENTIALS_FILE': 'credentials.json',
    'MAX_JOBS_TO_SCRAPE': 100,
    'JOB_SEARCH_SITES': ['linkedin', 'indeed'],
    'JOB_SEARCH_COUNTRY': 'India',
    'JOB_SEARCH_LOCATION': 'Bengaluru',
    'RESUME_SUMMARY_FILE': 'resume_summary.txt'
}


class BlockedDomainsManager:
    """Manage blocked email domains and addresses."""
    
    def __init__(self, file_path=CONFIG['BLOCKED_DOMAINS_FILE']):
        self.file_path = file_path
        self.data = self.load()
    
    def load(self):
        if Path(self.file_path).exists():
            with open(self.file_path, 'r') as f:
                return json.load(f)
        return {'blocked_domains': [], 'blocked_emails': []}
    
    def save(self):
        with open(self.file_path, 'w') as f:
            json.dump(self.data, f, indent=2)
    
    def add_domains(self, domains):
        for domain in domains:
            domain = domain.strip().lower()
            if domain and domain not in self.data['blocked_domains']:
                self.data['blocked_domains'].append(domain)
        self.save()
    
    def add_emails(self, emails):
        for email in emails:
            email = email.strip().lower()
            if email and email not in self.data['blocked_emails']:
                self.data['blocked_emails'].append(email)
        self.save()
    
    def is_blocked(self, email):
        email = email.strip().lower()
        if email in self.data['blocked_emails']:
            return True
        domain = email.split('@')[-1] if '@' in email else ''
        return domain in self.data['blocked_domains']


class SendingStateManager:
    """Track sending progress per sender email."""
    
    def __init__(self, sender_email):
        self.sender_email = sender_email.replace('@', '_at_').replace('.', '_')
        self.file_path = f'state_{self.sender_email}.json'
        self.state = self.load()
    
    def load(self):
        if Path(self.file_path).exists():
            with open(self.file_path, 'r') as f:
                return json.load(f)
        return {
            'last_index_sent': -1,
            'sent_emails': [],
            'last_run_date': None
        }
    
    def save(self):
        self.state['last_run_date'] = datetime.now().isoformat()
        with open(self.file_path, 'w') as f:
            json.dump(self.state, f, indent=2)
    
    def mark_sent(self, index, email):
        self.state['last_index_sent'] = index
        if email.lower() not in [e.lower() for e in self.state['sent_emails']]:
            self.state['sent_emails'].append(email.lower())
        self.save()
    
    def was_sent(self, email):
        return email.lower() in [e.lower() for e in self.state['sent_emails']]


class PerplexityClient:
    """Interact with Perplexity AI API."""
    
    def __init__(self, api_key=CONFIG['PPLX_API_KEY']):
        self.api_key = api_key
        try:
            self.client = OpenAI(api_key=api_key, base_url="https://api.perplexity.ai")
            self.use_openai_lib = True
        except:            self.use_openai_lib = False

    def query(self, prompt, system_prompt="You are a helpful assistant."):
        if self.use_openai_lib:
            try:
                response = self.client.chat.completions.create(
                    model=CONFIG['PPLX_MODEL'],
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ]
                )
                return response.choices[0].message.content
            except Exception as e:
                print(f"Error querying Perplexity: {e}")
                return None
        else:
            # Fallback to requests
            url = "https://api.perplexity.ai/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": CONFIG['PPLX_MODEL'],
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ]
            }
            try:
                response = requests.post(url, headers=headers, json=payload)
                response.raise_for_status()
                return response.json()['choices'][0]['message']['content']
            except Exception as e:
                print(f"Error querying Perplexity via requests: {e}")
                return None


class JobApplicationSystem:
    def __init__(self):
        self.pplx = PerplexityClient()
        self.gmail_service = None
        self.sender_email = None
        self.resume_path = None
        self.resume_summary = None

    def authenticate_gmail(self, sender_email):
        self.sender_email = sender_email
        creds_path = f"token_{sender_email.replace('@', '_at_').replace('.', '_')}.json"
        creds = None
        
        if os.path.exists(creds_path):
            creds = Credentials.from_authorized_user_file(creds_path, SCOPES)
            
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(CONFIG['CREDENTIALS_FILE']):
                    print(f"Error: {CONFIG['CREDENTIALS_FILE']} not found. Please provide Gmail API credentials.")
                    return False
                flow = InstalledAppFlow.from_client_secrets_file(CONFIG['CREDENTIALS_FILE'], SCOPES)
                creds = flow.run_local_server(port=0)
            
            with open(creds_path, 'w') as token:
                token.write(creds.to_json())
        
        try:
            self.gmail_service = build('gmail', 'v1', credentials=creds)
            return True
        except Exception as e:
            print(f"Error building Gmail service: {e}")
            return False

    def scrape_and_find_contacts(self, job_title, location):
        if not scrape_jobs:
            print("Job scraping tool not available.")
            return
            
        print(f"Scraping jobs for '{job_title}' in '{location}'...")
        jobs = scrape_jobs(
            site_name=CONFIG['JOB_SEARCH_SITES'],
            search_term=job_title,
            location=location,
            results_wanted=CONFIG['MAX_JOBS_TO_SCRAPE'],
            hours_old=72,
            country_indeed=CONFIG['JOB_SEARCH_COUNTRY']
        )
        
        print(f"Found {len(jobs)} jobs. Finding HR contacts...")
        recipients = []
        
        for _, job in jobs.iterrows():
            company = job.get('company', 'Unknown Company')
            title = job.get('title', 'Unknown Title')
            url = job.get('job_url', '')
            
            prompt = f"""
            Search for the HR Manager or Engineering Manager email for {company} which is currently hiring for {title}.
            The job listing is here: {url}.
            Try to find a specific person's name and email if possible.
            Return ONLY a JSON object with: 
            {{
                "company_name": "{company}",
                "recipient_name": "Name or 'HR Manager'",
                "designation": "Specific Role",
                "email": "email@example.com",
                "job_title": "{title}",
                "job_url": "{url}"
            }}
            If you cannot find an email, return null.
            """
            
            result_str = self.pplx.query(prompt, "You are a specialized contact finding assistant.")
            if result_str:
                try:
                    # Extract JSON from potential markdown
                    json_match = re.search(r'\{.*\}', result_str, re.DOTALL)
                    if json_match:
                        contact = json.loads(json_match.group(0))
                        if contact and contact.get('email'):
                            recipients.append(contact)
                            print(f"Found: {contact['email']} for {company}")
                except:
                    continue
        
        if recipients:
            df = pd.DataFrame(recipients)
            df.to_csv(CONFIG['RECIPIENTS_CSV'], index=False)
            print(f"Saved {len(recipients)} contacts to {CONFIG['RECIPIENTS_CSV']}")
        else:
            print("No contacts found.")

    def analyze_resume(self, resume_path):
        self.resume_path = resume_path
        # In a real scenario, use a PDF parser here. For this script, we'll ask the user to provide a text summary 
        # or we assume text content if provided.
        if os.path.exists(CONFIG['RESUME_SUMMARY_FILE']):
            with open(CONFIG['RESUME_SUMMARY_FILE'], 'r') as f:
                self.resume_summary = f.read()
        else:
            print("Please provide a brief text summary of your resume in 'resume_summary.txt' for the AI to use.")
            self.resume_summary = "A qualified professional looking for opportunities."

    def generate_email_content(self, recipient):
        prompt = f"""
        Generate a highly personalized job application email.
        Context:
        - Recipient Name: {recipient['recipient_name']}
        - Designation: {recipient['designation']}
        - Company: {recipient['company_name']}
        - Job Title: {recipient['job_title']}
        - Job Details: {recipient.get('job_url', 'No URL provided')}
        - My Resume Summary: {self.resume_summary}
        
        Requirements:
        - Subject line must be professional and catchy.
        - Body must mention specific requirements from the job if available.
        - Tone: Enthusiastic but professional.
        - Mention that a resume is attached.
        
        Output as JSON:
        {{
            "subject": "...",
            "body": "..."
        }}
        """
        
        result_str = self.pplx.query(prompt, "You are an expert career consultant and copywriter.")
        if result_str:
            try:
                json_match = re.search(r'\{.*\}', result_str, re.DOTALL)
                if json_match:
                    return json.loads(json_match.group(0))
            except:
                pass
        return None

    def send_email(self, recipient_email, subject, body, attachment_path=None):
        message = MIMEMultipart()
        message['to'] = recipient_email
        message['subject'] = subject
        
        msg = MIMEText(body)
        message.attach(msg)
        
        if attachment_path and os.path.exists(attachment_path):
            with open(attachment_path, "rb") as attachment:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(attachment.read())
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    f"attachment; filename= {os.path.basename(attachment_path)}",
                )
                message.attach(part)
        
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        
        try:
            self.gmail_service.users().messages().send(userId="me", body={'raw': raw_message}).execute()
            return True
        except HttpError as error:
            print(f"An error occurred: {error}")
            if error.resp.status in [403, 429]:
                print("Quota limit reached.")
                return "QUOTA_ERROR"
            return False

    def run_sending_process(self, resume_path, test_mode=False):
        if not self.gmail_service:
            print("Gmail service not authenticated.")
            return

        self.analyze_resume(resume_path)
        
        if not os.path.exists(CONFIG['RECIPIENTS_CSV']):
            print(f"Recipients file {CONFIG['RECIPIENTS_CSV']} not found.")
            return
            
        df = pd.read_csv(CONFIG['RECIPIENTS_CSV'])
        blocked_mgr = BlockedDomainsManager()
        state_mgr = SendingStateManager(self.sender_email)
        
        start_index = state_mgr.state['last_index_sent'] + 1
        print(f"Starting from index {start_index}...")
        
        for i in range(start_index, len(df)):
            row = df.iloc[i]
            email = row['email']
            
            if blocked_mgr.is_blocked(email) or state_mgr.was_sent(email):
                print(f"Skipping {email} (blocked or duplicate)")
                continue
                
            print(f"Processing {email} ({row['company_name']})...")
            
            content = self.generate_email_content(row)
            if not content:
                print(f"Failed to generate content for {email}")
                continue
                
            if test_mode:
                print(f"--- TEST MODE ---")
                print(f"To: {email}")
                print(f"Subject: {content['subject']}")
                print(f"Body: {content['body'][:100]}...")
                print(f"-----------------")
                if i >= start_index + 2: break # Limit test mode output
                continue
                
            result = self.send_email(email, content['subject'], content['body'], resume_path)
            
            if result == True:
                print(f"Sent successfully to {email}")
                state_mgr.mark_sent(i, email)
            elif result == "QUOTA_ERROR":
                print("Stopping due to quota.")
                break
            else:
                print(f"Failed to send to {email}")
            
            time.sleep(2) # Avoid rapid-fire triggers


def main():
    system = JobApplicationSystem()
    
    print("=== AI Job Application Mailer ===")
    mode = input("Select Mode (prepare/send/both): ").strip().lower()
    
    sender_email = input("Enter your Gmail address: ").strip()
    if not system.authenticate_gmail(sender_email):
        return

    blocked_mgr = BlockedDomainsManager()
    if input("Update blocked domains/emails? (y/n): ").lower() == 'y':
        domains = input("Enter domains to block (comma separated): ").split(',')
        blocked_mgr.add_domains(domains)

    if mode in ['prepare', 'both']:
        job_title = input("What job title/tech stack are you looking for? (e.g. 'React.js developer'): ")
        location = input(f"Location (default {CONFIG['JOB_SEARCH_LOCATION']}): ") or CONFIG['JOB_SEARCH_LOCATION']
        system.scrape_and_find_contacts(job_title, location)
        
    if mode in ['send', 'both']:
        resume_path = input("Path to your resume PDF: ").strip()
        test_mode = input("Run in test mode? (y/n): ").lower() == 'y'
        system.run_sending_process(resume_path, test_mode)

if __name__ == "__main__":
    main()
