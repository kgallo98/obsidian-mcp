#!/usr/bin/env python3
"""
selfroot Obsidian MCP Server
Gives Claude Code read/write access to the Obsidian vault via LiveSync CouchDB.

Notes are stored in CouchDB by Obsidian LiveSync plugin:
  - Note docs (type "plain"): metadata + children[] array of leaf IDs
  - Leaf docs (type "leaf", h:* IDs): actual content chunks
  - To read a note: fetch doc → fetch each child leaf → concatenate data

Usage (in ~/.claude/settings.json mcpServers):
  "command": "/opt/homebrew/bin/python3.12",
  "args": ["/Users/selfroot/selfroot/services/obsidian-mcp/server.py"]
"""

import os
import json
import urllib.request
import urllib.error
import base64
import hashlib
import time
from datetime import datetime
from mcp.server.fastmcp import FastMCP

# ── Config ───────────────────────────────────────────────────────────────────
COUCH_URL  = os.environ.get("OBSIDIAN_COUCH_URL", "http://127.0.0.1:5984")
COUCH_DB   = os.environ.get("OBSIDIAN_COUCH_DB", "obsidian")
COUCH_USER = os.environ.get("OBSIDIAN_COUCH_USER", "admin")
COUCH_PASS = os.environ.get("OBSIDIAN_COUCH_PASS", "")

mcp = FastMCP("obsidian-vault")

# ── CouchDB helpers ──────────────────────────────────────────────────────────

def _auth_header():
    creds = base64.b64encode(f"{COUCH_USER}:{COUCH_PASS}".encode()).decode()
    return f"Basic {creds}"

def _couch_get(path):
    """GET request to CouchDB, returns parsed JSON or None."""
    url = f"{COUCH_URL}/{COUCH_DB}/{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": _auth_header(),
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        return None

def _couch_post(path, body):
    """POST request to CouchDB, returns parsed JSON or None."""
    url = f"{COUCH_URL}/{COUCH_DB}/{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": _auth_header(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError):
        return None

def _couch_put(doc_id, body):
    """PUT a document to CouchDB, returns parsed JSON or raises."""
    url = f"{COUCH_URL}/{COUCH_DB}/{urllib.request.quote(doc_id, safe='')}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="PUT", headers={
        "Authorization": _auth_header(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())

def _couch_delete(doc_id, rev):
    """DELETE a document from CouchDB."""
    url = f"{COUCH_URL}/{COUCH_DB}/{urllib.request.quote(doc_id, safe='')}?rev={rev}"
    req = urllib.request.Request(url, method="DELETE", headers={
        "Authorization": _auth_header(),
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())

def _generate_leaf_id():
    """Generate a unique leaf ID in LiveSync format (h:<random>)."""
    raw = hashlib.md5(f"{time.time_ns()}".encode()).hexdigest()[:13]
    return f"h:{raw}"

def _write_note_to_couch(path, content):
    """Write or update a note in CouchDB using LiveSync document format.
    Returns (success: bool, message: str)."""
    now_ms = int(time.time() * 1000)
    doc_id = path.lower()
    leaf_id = _generate_leaf_id()

    # Create the leaf (content chunk)
    _couch_put(leaf_id, {"data": content, "type": "leaf"})

    # Check if note already exists
    existing = _couch_get(urllib.request.quote(doc_id, safe=""))
    if existing and existing.get("type") == "plain":
        # Delete old leaves
        for old_leaf_id in existing.get("children", []):
            old_leaf = _couch_get(urllib.request.quote(old_leaf_id, safe=""))
            if old_leaf and "_rev" in old_leaf:
                try:
                    _couch_delete(old_leaf_id, old_leaf["_rev"])
                except Exception:
                    pass
        # Update existing doc
        existing["children"] = [leaf_id]
        existing["mtime"] = now_ms
        existing["size"] = len(content)
        _couch_put(doc_id, existing)
        return True, f"Updated note: {path}"
    else:
        # Create new note doc
        _couch_put(doc_id, {
            "children": [leaf_id],
            "path": path,
            "ctime": now_ms,
            "mtime": now_ms,
            "size": len(content),
            "type": "plain",
            "eden": {},
        })
        return True, f"Created note: {path}"

def _reassemble_note(doc):
    """Fetch all leaf children and concatenate their data to get note content."""
    children = doc.get("children", [])
    if not children:
        return ""
    parts = []
    for child_id in children:
        leaf = _couch_get(urllib.request.quote(child_id, safe=""))
        if leaf and "data" in leaf:
            parts.append(leaf["data"] if isinstance(leaf["data"], str) else "")
    return "".join(parts)

def _format_timestamp(ms):
    """Convert millisecond epoch to readable date string."""
    if not ms:
        return "unknown"
    try:
        return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError):
        return "unknown"

def _get_all_notes():
    """Fetch all note documents (type=plain, not deleted) from CouchDB."""
    result = _couch_post("_find", {
        "selector": {"type": "plain"},
        "fields": ["_id", "path", "ctime", "mtime", "size", "children", "deleted"],
        "limit": 500,
    })
    if not result or "docs" not in result:
        return []
    return [d for d in result["docs"] if not d.get("deleted")]

# ── MCP Tools ────────────────────────────────────────────────────────────────

@mcp.tool()
def list_notes(folder: str = "") -> str:
    """List all notes in the Obsidian vault. Optionally filter by folder prefix.

    Args:
        folder: Optional folder path prefix to filter (e.g. "Projects/" or "Daily/")

    Returns:
        List of notes with path, size, and modification date.
    """
    notes = _get_all_notes()
    if folder:
        folder = folder.rstrip("/") + "/"
        notes = [n for n in notes if n.get("path", "").startswith(folder)]

    if not notes:
        return "No notes found" + (f" in folder '{folder}'" if folder else "") + "."

    notes.sort(key=lambda n: n.get("mtime", 0), reverse=True)
    lines = [f"Found {len(notes)} note(s):\n"]
    for n in notes:
        path = n.get("path", n.get("_id", "?"))
        modified = _format_timestamp(n.get("mtime"))
        size = n.get("size", 0)
        lines.append(f"  {path}  ({size} bytes, modified {modified})")
    return "\n".join(lines)

@mcp.tool()
def read_note(path: str) -> str:
    """Read the full content of an Obsidian note by its path.

    Args:
        path: The note path as shown by list_notes (e.g. "selfroot.md" or "Projects/ideas.md")

    Returns:
        The full markdown content of the note.
    """
    # Try exact ID match (lowercase)
    doc = _couch_get(urllib.request.quote(path.lower(), safe=""))
    if not doc or doc.get("type") != "plain":
        # Try as-is
        doc = _couch_get(urllib.request.quote(path, safe=""))
    if not doc or doc.get("type") != "plain":
        # Search by path field
        result = _couch_post("_find", {
            "selector": {"type": "plain", "path": path},
            "limit": 1,
        })
        if result and result.get("docs"):
            doc = result["docs"][0]

    if not doc or doc.get("type") != "plain":
        return f"Note not found: {path}"

    if doc.get("deleted"):
        return f"Note '{path}' has been deleted."

    content = _reassemble_note(doc)
    if not content:
        return f"Note '{path}' exists but has no content."

    header = f"# {doc.get('path', path)}\n"
    header += f"Modified: {_format_timestamp(doc.get('mtime'))}\n"
    header += f"Created: {_format_timestamp(doc.get('ctime'))}\n"
    header += "---\n\n"
    return header + content

@mcp.tool()
def search_notes(query: str, limit: int = 10) -> str:
    """Search across all Obsidian notes for a text string.

    Args:
        query: Text to search for (case-insensitive substring match)
        limit: Maximum number of results (default 10)

    Returns:
        Matching notes with context snippets around each match.
    """
    if not query or len(query) < 2:
        return "Search query must be at least 2 characters."

    notes = _get_all_notes()
    query_lower = query.lower()
    matches = []

    for note in notes:
        content = _reassemble_note(note)
        path = note.get("path", note.get("_id", ""))
        # Search both content and path
        content_lower = content.lower() if content else ""
        path_lower = path.lower()
        idx = content_lower.find(query_lower)
        path_match = query_lower in path_lower
        if idx == -1 and not path_match:
            continue
        if idx == -1:
            idx = 0  # show beginning of note for path-only matches

        # Extract snippet around match
        start = max(0, idx - 80)
        end = min(len(content), idx + len(query) + 80)
        snippet = content[start:end].replace("\n", " ").strip()
        if start > 0:
            snippet = "..." + snippet
        if end < len(content):
            snippet = snippet + "..."

        matches.append({
            "path": note.get("path", note.get("_id", "?")),
            "modified": _format_timestamp(note.get("mtime")),
            "snippet": snippet,
        })
        if len(matches) >= limit:
            break

    if not matches:
        return f"No notes matching '{query}'."

    lines = [f"Found {len(matches)} note(s) matching '{query}':\n"]
    for m in matches:
        lines.append(f"  {m['path']}  (modified {m['modified']})")
        lines.append(f"    > {m['snippet']}")
        lines.append("")
    return "\n".join(lines)

@mcp.tool()
def recent_notes(limit: int = 5) -> str:
    """Get the most recently modified notes from the Obsidian vault.

    Args:
        limit: Number of recent notes to return (default 5, max 20)

    Returns:
        Recent notes with path, date, and a content preview.
    """
    limit = min(max(1, limit), 20)
    notes = _get_all_notes()
    notes.sort(key=lambda n: n.get("mtime", 0), reverse=True)
    notes = notes[:limit]

    if not notes:
        return "No notes in vault."

    lines = [f"Most recent {len(notes)} note(s):\n"]
    for n in notes:
        path = n.get("path", n.get("_id", "?"))
        modified = _format_timestamp(n.get("mtime"))
        size = n.get("size", 0)
        # Get brief preview
        content = _reassemble_note(n)
        preview = content[:150].replace("\n", " ").strip() if content else "(empty)"
        if len(content) > 150:
            preview += "..."

        lines.append(f"  {path}  ({size} bytes, modified {modified})")
        lines.append(f"    > {preview}")
        lines.append("")
    return "\n".join(lines)

@mcp.tool()
def write_note(path: str, content: str) -> str:
    """Create or overwrite a note in the Obsidian vault.

    The note will sync to all connected Obsidian clients via LiveSync.
    Use folder prefixes to organize (e.g. "claude/session-summary.md").

    Args:
        path: Note path including .md extension (e.g. "claude/plan.md", "ideas/feature.md")
        content: Full markdown content for the note

    Returns:
        Confirmation message with the note path.

    Security note (S38 P4.3 / LO-04): `path` is used as a CouchDB document
    ID (NOT a filesystem path). Strings like "../../etc/passwd" simply create
    a doc with that name and are not exploitable as path traversal — the
    URL-encoding in `_couch_put` neutralises special characters. **If this
    backend ever moves to a real filesystem, ADD path validation here**
    (reject `..`, absolute paths, anything outside the vault root) before
    persisting.
    """
    if not path.endswith(".md"):
        path += ".md"
    try:
        ok, msg = _write_note_to_couch(path, content)
        return msg
    except Exception as e:
        return f"Failed to write note '{path}': {e}"

@mcp.tool()
def append_to_note(path: str, content: str) -> str:
    """Append content to an existing note, or create it if it doesn't exist.

    Args:
        path: Note path (e.g. "claude/log.md")
        content: Markdown content to append (will be added after a newline)

    Returns:
        Confirmation message.
    """
    if not path.endswith(".md"):
        path += ".md"

    # Try to read existing content
    doc_id = path.lower()
    doc = _couch_get(urllib.request.quote(doc_id, safe=""))
    existing_content = ""
    if doc and doc.get("type") == "plain":
        existing_content = _reassemble_note(doc)

    new_content = existing_content + "\n" + content if existing_content else content
    try:
        ok, msg = _write_note_to_couch(path, new_content)
        action = "Appended to" if existing_content else "Created"
        return f"{action} note: {path}"
    except Exception as e:
        return f"Failed to append to note '{path}': {e}"

@mcp.tool()
def check_inbox() -> str:
    """Check the claude/ folder for notes from the user.

    This is the primary way users send context, plans, or instructions to Claude
    via Obsidian. Notes in claude/ are treated as input for the current session.

    Returns:
        Contents of all notes in the claude/ folder, or a message if empty.
    """
    notes = _get_all_notes()
    inbox = [n for n in notes if n.get("path", "").startswith("claude/")]
    inbox.sort(key=lambda n: n.get("mtime", 0), reverse=True)

    if not inbox:
        return "No notes in claude/ folder."

    lines = [f"Found {len(inbox)} note(s) in claude/ inbox:\n"]
    for n in inbox:
        path = n.get("path", "?")
        modified = _format_timestamp(n.get("mtime"))
        content = _reassemble_note(n)
        lines.append(f"## {path}  (modified {modified})")
        lines.append(content if content else "(empty)")
        lines.append("")
    return "\n".join(lines)

# ── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not COUCH_PASS:
        print("WARNING: OBSIDIAN_COUCH_PASS not set — CouchDB auth will fail")
    mcp.run()
