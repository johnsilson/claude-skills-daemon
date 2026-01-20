#!/usr/bin/env python3
"""
Claude Skills Daemon
Watches a folder for skill output files and automatically appends them to Google Docs.
Runs continuously in background via launchd.

FIXES:
1. Better file write detection with exponential backoff
2. File permission correction (sets to 644 after reading)
3. Longer wait times for file completion
4. Better error handling for file access issues
"""

import os
import json
import time
import logging
import glob
import fnmatch
from pathlib import Path
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from google.oauth2 import service_account
import googleapiclient.discovery

# Configuration
CONFIG_PATH = os.path.expanduser("~/.claude-skills-config.json")
LOG_PATH = os.path.expanduser("~/Library/Logs/claude-skills-daemon.log")

# Set up logging with proper configuration
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Clear any existing handlers
logger.handlers = []

# File handler
file_handler = logging.FileHandler(LOG_PATH)
file_handler.setLevel(logging.INFO)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# Format
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

logger.addHandler(file_handler)
logger.addHandler(console_handler)

class SkillsFileHandler(FileSystemEventHandler):
    """Handles file events in the watched folder"""
    
    def __init__(self, config):
        self.config = config
        self.service_account_creds = None
        self.docs_service = None
        self.last_processed = set()
    
    def on_created(self, event):
        """Handle file creation events"""
        if event.is_directory:
            return
        
        filename = os.path.basename(event.src_path)
        logger.info(f"on_created event detected: {filename}")
        
        # Wait for file to be completely written
        if self._wait_for_file_ready(event.src_path):
            self.process_file(event.src_path)
    
    def on_modified(self, event):
        """Handle file modification events (catches browser downloads)"""
        if event.is_directory:
            return
        
        filename = os.path.basename(event.src_path)
        # Only process if it matches a skill pattern
        if self._matches_any_pattern(filename):
            logger.info(f"on_modified event detected: {filename}")
            
            # Wait for file to be completely written
            if self._wait_for_file_ready(event.src_path):
                self.process_file(event.src_path)
    
    def _wait_for_file_ready(self, filepath, max_retries=10):
        """
        Wait for file to be completely written using size stability check.
        Returns True if file is ready, False if timeout.
        """
        if not os.path.exists(filepath):
            return False
        
        last_size = -1
        retry_count = 0
        wait_time = 0.1  # Start with 100ms
        
        while retry_count < max_retries:
            try:
                current_size = os.path.getsize(filepath)
                
                if current_size == last_size and current_size > 0:
                    # Size hasn't changed and file has content
                    logger.info(f"File ready: {os.path.basename(filepath)} ({current_size} bytes)")
                    return True
                
                last_size = current_size
                time.sleep(wait_time)
                
                # Exponential backoff
                wait_time = min(wait_time * 1.5, 2.0)  # Cap at 2 seconds
                retry_count += 1
                
            except (OSError, IOError) as e:
                logger.warning(f"Error checking file size (retry {retry_count}): {str(e)}")
                time.sleep(wait_time)
                retry_count += 1
        
        logger.warning(f"File not ready after {max_retries} retries: {filepath}")
        return False
    
    def _matches_any_pattern(self, filename):
        """Check if filename matches any skill pattern"""
        for skill_name, skill_config in self.config.get('skills', {}).items():
            pattern = skill_config.get('pattern')
            if pattern and fnmatch.fnmatch(filename, pattern):
                return True
        return False
    
    def process_file(self, filepath):
        """Process a file and append to appropriate Google Doc"""
        filename = os.path.basename(filepath)
        
        # Prevent duplicate processing
        file_id = f"{filepath}:{os.path.getmtime(filepath)}"
        if file_id in self.last_processed:
            logger.info(f"Skipping already processed file: {filename}")
            return
        
        try:
            # Match file to skill
            skill_config = self._match_file_to_skill(filename)
            if not skill_config:
                logger.info(f"No matching skill for file: {filename}")
                return
            
            logger.info(f"Matched skill: {skill_config['name']} for file: {filename}")
            
            # Read file content with retry logic
            content = self._read_file_with_retry(filepath)
            if not content:
                logger.error(f"Failed to read file: {filename}")
                return
            
            # Fix file permissions after reading
            try:
                os.chmod(filepath, 0o644)
            except Exception as e:
                logger.warning(f"Could not fix file permissions: {str(e)}")
            
            # Initialize Google Docs service if needed
            if not self.docs_service:
                self._init_google_service()
            
            # Append to Google Doc
            doc_id = skill_config['doc_id']
            self._append_to_doc(doc_id, content, skill_config['name'])
            
            # Mark as processed
            self.last_processed.add(file_id)
            
            # Archive the file
            self._archive_file(filepath)
            
            logger.info(f"Successfully processed: {filename}")
            
        except Exception as e:
            logger.error(f"Error processing file {filename}: {str(e)}")
    
    def _read_file_with_retry(self, filepath, max_retries=5):
        """Read file with retry logic for permission issues"""
        for attempt in range(max_retries):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                return content
            except PermissionError as e:
                logger.warning(f"Permission denied reading file (attempt {attempt + 1}/{max_retries}): {filepath}")
                if attempt < max_retries - 1:
                    time.sleep(0.5 * (attempt + 1))  # Increasing delays
                else:
                    raise
            except Exception as e:
                logger.error(f"Error reading file: {str(e)}")
                raise
        
        return None
    
    def _match_file_to_skill(self, filename):
        """Match filename to a skill configuration"""
        for skill_name, skill_config in self.config.get('skills', {}).items():
            pattern = skill_config.get('pattern')
            if pattern and fnmatch.fnmatch(filename, pattern):
                return {
                    'name': skill_name,
                    'doc_id': skill_config['doc_id'],
                    'skill_name': skill_config.get('skill_name', skill_name)
                }
        return None
    
    def _init_google_service(self):
        """Initialize Google Docs service with service account"""
        try:
            # Get service account file path from config
            service_account_file = os.path.expanduser(
                self.config.get('service_account_file', 
                               '~/Documents/Family/AI Tools/Claude-Skills-Files/service-account-key.json')
            )
            
            # Create credentials
            self.service_account_creds = service_account.Credentials.from_service_account_file(
                service_account_file,
                scopes=['https://www.googleapis.com/auth/documents']
            )
            
            # Build service
            self.docs_service = googleapiclient.discovery.build(
                'docs', 'v1', credentials=self.service_account_creds
            )
            
            logger.info("Google Docs service initialized successfully")
            
        except Exception as e:
            logger.error(f"Error initializing Google service: {str(e)}")
            raise
    
    def _append_to_doc(self, doc_id, content, skill_name):
        """Append content to a Google Doc"""
        try:
            # Get current document to find end index
            doc = self.docs_service.documents().get(documentId=doc_id).execute()
            end_index = doc.get('body').get('content')[-1].get('endIndex') - 1
            
            # Prepare content with separators
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            separator = f"\n\n{'=' * 80}\n"
            header = f"Added by {skill_name} daemon at {timestamp}\n"
            footer = f"\n{'=' * 80}\n\n"
            
            full_content = separator + header + separator + content + footer
            
            # Insert content at end of document
            requests = [
                {
                    'insertText': {
                        'location': {'index': end_index},
                        'text': full_content
                    }
                }
            ]
            
            self.docs_service.documents().batchUpdate(
                documentId=doc_id,
                body={'requests': requests}
            ).execute()
            
            logger.info(f"Content appended to doc: {doc_id}")
            
        except Exception as e:
            logger.error(f"Error appending to doc: {str(e)}")
            raise
    
    def _archive_file(self, filepath):
        """Move processed file to archive folder"""
        try:
            archive_folder = os.path.expanduser(
                self.config.get('archive_folder', '~/Downloads')
            )
            
            # Create archive folder if it doesn't exist
            os.makedirs(archive_folder, exist_ok=True)
            
            # Move file
            filename = os.path.basename(filepath)
            archive_path = os.path.join(archive_folder, filename)
            
            # If archive path is same as source, just log it
            if os.path.normpath(archive_path) == os.path.normpath(filepath):
                logger.info(f"File remains in watch folder (archive=watch): {filename}")
            else:
                os.rename(filepath, archive_path)
                logger.info(f"File archived to: {archive_path}")
            
        except Exception as e:
            logger.error(f"Error archiving file: {str(e)}")

def load_config():
    """Load configuration from JSON file"""
    if not os.path.exists(CONFIG_PATH):
        logger.error(f"Config file not found: {CONFIG_PATH}")
        raise FileNotFoundError(f"Please create {CONFIG_PATH}")
    
    with open(CONFIG_PATH, 'r') as f:
        config = json.load(f)
    
    logger.info("Configuration loaded successfully")
    return config

def main():
    """Main daemon loop"""
    logger.info("=" * 60)
    logger.info("Claude Skills Daemon starting")
    logger.info("=" * 60)
    
    try:
        # Load configuration
        config = load_config()
        
        # Create watch folder if it doesn't exist
        watch_folder = os.path.expanduser(config.get('watch_folder'))
        os.makedirs(watch_folder, exist_ok=True)
        
        logger.info(f"Watching folder: {watch_folder}")
        
        # Set up file system observer
        event_handler = SkillsFileHandler(config)
        observer = Observer()
        observer.schedule(event_handler, watch_folder, recursive=False)
        observer.start()
        
        logger.info("Daemon is running. Watching for file events...")
        
        # Keep daemon running
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Stopping daemon...")
            observer.stop()
            observer.join()
            logger.info("Daemon stopped successfully")
        
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        raise

if __name__ == '__main__':
    main()
