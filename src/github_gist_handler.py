"""
GitHub Gist Integration Handler for SaveMe Bot
"""

import os
import json
import logging
from typing import Optional, Dict, Any
from github import Github, GithubException
from github.Gist import Gist
from github.InputFileContent import InputFileContent

logger = logging.getLogger(__name__)

class GithubGistHandler:
    """Handler for creating and managing GitHub Gists"""
    
    def __init__(self, database):
        """Initialize the Gist handler with database connection"""
        self.db = database
        
    def get_user_token(self, user_id: int) -> Optional[str]:
        """Get stored GitHub token for user"""
        user_settings = self.db.get_user_settings(user_id)
        if user_settings:
            return user_settings.get('github_token')
        return None
    
    def set_user_token(self, user_id: int, token: str) -> bool:
        """Store GitHub token for user"""
        try:
            # Validate token by trying to authenticate
            g = Github(token)
            user = g.get_user()
            username = user.login  # This will raise an exception if token is invalid
            
            # Store token in database
            self.db.set_user_setting(user_id, 'github_token', token)
            self.db.set_user_setting(user_id, 'github_username', username)
            return True
        except Exception as e:
            logger.error(f"Failed to validate GitHub token: {e}")
            return False
    
    def remove_user_token(self, user_id: int) -> bool:
        """Remove stored GitHub token for user"""
        try:
            self.db.remove_user_setting(user_id, 'github_token')
            self.db.remove_user_setting(user_id, 'github_username')
            return True
        except Exception as e:
            logger.error(f"Failed to remove GitHub token: {e}")
            return False
    
    def create_gist_from_item(self, user_id: int, item_id: int, public: bool = False, 
                             description: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Create a GitHub Gist from a saved item
        
        Args:
            user_id: Telegram user ID
            item_id: Database item ID
            public: Whether the gist should be public (default: False)
            description: Optional description for the gist
            
        Returns:
            Dict with gist info or None if failed
        """
        try:
            # Get user token
            token = self.get_user_token(user_id)
            if not token:
                return {"error": "GitHub token not configured. Use /setup_github first."}
            
            # Get item from database
            item = self.db.get_item_by_id(item_id)
            if not item or item['user_id'] != user_id:
                return {"error": "Item not found or access denied."}
            
            # Check if item is code/text
            if item['content_type'] not in ['text', 'code']:
                return {"error": "Only text and code items can be converted to Gists."}
            
            # Prepare gist content
            content = item['content']
            filename = self._generate_filename(item)
            
            # Use item subject as description if not provided
            if not description:
                description = f"{item['subject']} - Created by SaveMe Bot"
            
            # Create gist
            g = Github(token)
            
            # Create files dict for gist
            files = {filename: InputFileContent(content)}
            
            # Create the gist
            gist = g.get_user().create_gist(
                public=public,
                files=files,
                description=description
            )
            
            # Store gist URL in database
            self.db.add_gist_to_item(item_id, gist.html_url, gist.id)
            
            return {
                "success": True,
                "url": gist.html_url,
                "gist_id": gist.id,
                "filename": filename,
                "public": public
            }
            
        except GithubException as e:
            logger.error(f"GitHub API error: {e}")
            return {"error": f"GitHub API error: {str(e)}"}
        except Exception as e:
            logger.error(f"Failed to create gist: {e}")
            return {"error": f"Failed to create gist: {str(e)}"}
    
    def update_gist(self, user_id: int, gist_id: str, content: str, 
                   filename: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Update an existing gist"""
        try:
            token = self.get_user_token(user_id)
            if not token:
                return {"error": "GitHub token not configured."}
            
            g = Github(token)
            gist = g.get_gist(gist_id)
            
            # Get the first file if filename not specified
            if not filename:
                filename = list(gist.files.keys())[0]
            
            # Update the gist
            gist.edit(files={filename: InputFileContent(content)})
            
            return {
                "success": True,
                "url": gist.html_url,
                "gist_id": gist_id
            }
            
        except Exception as e:
            logger.error(f"Failed to update gist: {e}")
            return {"error": f"Failed to update gist: {str(e)}"}
    
    def delete_gist(self, user_id: int, gist_id: str) -> bool:
        """Delete a gist"""
        try:
            token = self.get_user_token(user_id)
            if not token:
                return False
            
            g = Github(token)
            gist = g.get_gist(gist_id)
            gist.delete()
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete gist: {e}")
            return False
    
    def get_user_gists(self, user_id: int, limit: int = 10) -> Optional[list]:
        """Get list of user's recent gists"""
        try:
            token = self.get_user_token(user_id)
            if not token:
                return None
            
            g = Github(token)
            user = g.get_user()
            gists = []
            
            for gist in user.get_gists()[:limit]:
                gists.append({
                    "id": gist.id,
                    "url": gist.html_url,
                    "description": gist.description or "No description",
                    "public": gist.public,
                    "created_at": gist.created_at.isoformat(),
                    "files": list(gist.files.keys())
                })
            
            return gists
            
        except Exception as e:
            logger.error(f"Failed to get user gists: {e}")
            return None
    
    def _generate_filename(self, item: Dict[str, Any]) -> str:
        """Generate appropriate filename for the gist based on content"""
        # Try to detect language from content
        content = item.get('content', '')
        subject = item.get('subject', 'code')
        
        # Clean subject for filename
        filename_base = "".join(c for c in subject if c.isalnum() or c in (' ', '-', '_')).rstrip()
        filename_base = filename_base.replace(' ', '_')[:50]  # Limit length
        
        if not filename_base:
            filename_base = "code"
        
        # Try to detect file extension
        extension = self._detect_extension(content)
        
        return f"{filename_base}{extension}"
    
    def _detect_extension(self, content: str) -> str:
        """Detect appropriate file extension based on content"""
        # Import the detect_code_language function from main.py
        from main import detect_code_language
        
        lang = detect_code_language(content)
        
        # Map language to extension
        extensions = {
            'python': '.py',
            'javascript': '.js',
            'java': '.java',
            'csharp': '.cs',
            'go': '.go',
            'rust': '.rs',
            'php': '.php',
            'html': '.html',
            'css': '.css',
            'sql': '.sql',
            'bash': '.sh',
            'dockerfile': '.dockerfile',
            'yaml': '.yaml',
            'json': '.json',
            'xml': '.xml',
            'markdown': '.md',
            'ini': '.ini',
            'cpp': '.cpp',
            'c': '.c',
            'ruby': '.rb',
            'swift': '.swift',
            'kotlin': '.kt',
            'typescript': '.ts'
        }
        
        return extensions.get(lang, '.txt')