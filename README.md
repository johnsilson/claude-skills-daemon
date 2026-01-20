# Claude Skills Daemon

Python daemon that orchestrates Claude API workflows with persistent storage in Google Drive and Gmail integration for automated AI-powered workflows.

## Overview

Automated system that runs scheduled Claude-powered workflows, managing state across conversation sessions using Google Drive as persistent storage and Gmail for data retrieval and processing.

## Key Features

- **Daemon Process**: Runs scheduled AI workflows automatically
- **Persistent Memory**: Uses Google Drive for state management across sessions
- **Email Integration**: Gmail API integration for automated email processing
- **Multi-API Orchestration**: Coordinates Claude API with Google services
- **Stateful Conversations**: Maintains context across multiple Claude API calls

## Architecture

The daemon monitors trigger files in Google Drive, executes Claude API workflows with appropriate context, and stores results back to Drive for continuity. This enables complex, multi-step AI workflows that maintain state over time.

## Use Cases

- Automated news briefings with historical tracking
- Stock analysis with email-based data feeds
- Scheduled report generation with persistent context
- [Add your specific use cases]

## Technical Stack

- Python 3.x
- Anthropic Claude API
- Google Drive API
- Gmail API
- [Other dependencies]

## Background

Built to demonstrate how AI can be effectively integrated into automated workflows with persistent memory and multi-source data integration.
