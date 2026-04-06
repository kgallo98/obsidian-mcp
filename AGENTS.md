# obsidian-mcp — Agent Instructions

Knowledge tree: `agents/`. Read the relevant file before working on a domain.

```
agents/
  _index.md          (routing node)
  active-context.md  (session handoff state)
  README.md          (tree sitemap)
```

## Session Protocol

1. Read `agents/active-context.md` for current work state
2. Find context: `agents/_index.md` → domain file
3. Work. Write learnings back to agent files when done.
4. Add session log entry: date + what changed + what was learned

## Routing

| Topic | File | Keywords |
|-------|------|----------|
| Current work state | active-context.md | current, now, status, what's next |

## Rules

- Write it down. Never rely on conversation memory.
- Update Known Issues when you hit a gotcha.
- Update Commands & Patterns when you find a useful technique.
- Creating a new file? Add routing entry to _index.md.
- File > 750 lines? Split at ## boundaries before adding more.

## Standard File Structure

```markdown
# Topic Name
## Purpose
One line.
## [Content]
## Known Issues
(Add as discovered)
## Commands & Patterns
(Copy-pasteable)
## Session Log
### YYYY-MM-DD — Description
- What changed, what was learned
```
