#!/usr/bin/env python3
"""
Notebook Library Management for NotebookLM
"""

import json
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime

from quaderno_companion.notebooklm.config import LIBRARY_FILE, DATA_DIR


class NotebookLibrary:
    """Manages a collection of NotebookLM notebooks with metadata"""

    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.library_file = LIBRARY_FILE
        self.notebooks: Dict[str, Dict[str, Any]] = {}
        self.active_notebook_id: Optional[str] = None
        self._load_library()

    def _load_library(self):
        if self.library_file.exists():
            try:
                with open(self.library_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.notebooks = data.get('notebooks', {})
                    self.active_notebook_id = data.get('active_notebook_id')
            except Exception as e:
                self.notebooks = {}
                self.active_notebook_id = None
        else:
            self._save_library()

    def _save_library(self):
        try:
            data = {
                'notebooks': self.notebooks,
                'active_notebook_id': self.active_notebook_id,
                'updated_at': datetime.now().isoformat()
            }
            with open(self.library_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"❌ Error saving library: {e}")

    def add_notebook(
        self,
        url: str,
        name: str,
        description: str,
        topics: List[str],
        content_types: Optional[List[str]] = None,
        use_cases: Optional[List[str]] = None,
        tags: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        notebook_id = name.lower().replace(' ', '-').replace('_', '-')
        if notebook_id in self.notebooks:
            raise ValueError(f"Notebook with ID '{notebook_id}' already exists")

        notebook = {
            'id': notebook_id,
            'url': url,
            'name': name,
            'description': description,
            'topics': topics,
            'content_types': content_types or [],
            'use_cases': use_cases or [],
            'tags': tags or [],
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat()
        }

        self.notebooks[notebook_id] = notebook
        if not self.active_notebook_id:
            self.active_notebook_id = notebook_id

        self._save_library()
        return notebook

    def get_notebook(self, notebook_id: str) -> Optional[Dict[str, Any]]:
        return self.notebooks.get(notebook_id)

    def get_active_notebook(self) -> Optional[Dict[str, Any]]:
        if self.active_notebook_id:
            return self.get_notebook(self.active_notebook_id)
        return None

    def list_notebooks(self) -> List[Dict[str, Any]]:
        return list(self.notebooks.values())

    def remove_notebook(self, notebook_id: str) -> bool:
        if notebook_id in self.notebooks:
            del self.notebooks[notebook_id]
            if self.active_notebook_id == notebook_id:
                self.active_notebook_id = next(iter(self.notebooks.keys())) if self.notebooks else None
            self._save_library()
            return True
        return False


def main():
    parser = argparse.ArgumentParser(description='Manage NotebookLM notebooks')
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    list_parser = subparsers.add_parser('list', help='List all notebooks')
    add_parser = subparsers.add_parser('add', help='Add a notebook')
    add_parser.add_argument('--url', required=True, help='Notebook URL')
    add_parser.add_argument('--name', required=True, help='Notebook name')
    add_parser.add_argument('--description', default='', help='Description')
    add_parser.add_argument('--topics', default='', help='Comma-separated topics')

    args = parser.parse_args()
    library = NotebookLibrary()

    if args.command == 'list':
        for nb in library.list_notebooks():
            mark = " [ACTIVE]" if nb['id'] == library.active_notebook_id else ""
            print(f"- {nb['id']}: {nb['name']}{mark} ({nb['url']})")
    elif args.command == 'add':
        topics = [t.strip() for t in args.topics.split(',') if t.strip()]
        nb = library.add_notebook(url=args.url, name=args.name, description=args.description, topics=topics)
        print(f"✅ Added notebook: {nb['name']}")


if __name__ == "__main__":
    main()
