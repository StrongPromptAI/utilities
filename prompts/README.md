# Task Prompts

Ready-to-paste prompts for each task in `thoughts.json`.

## Structure

```
prompts/
├── prompt-{id}.md    # Executable prompt for task {id}
├── prompt-2.md       # Railway staging setup (mainstreak)
├── prompt-8.md       # StrongPrompt verticals update
├── prompt-21.md      # DME data import (lora_training)
└── ...
```

## Usage

1. **View lean task list**:
   ```bash
   cat ~/repos/utilities/thoughts.json | jq '.[] | {id, thought, project, resource, status}'
   ```

2. **Get task with prompt**:
   ```bash
   TASK_ID=21
   TASK=$(jq ".[] | select(.id == $TASK_ID)" thoughts.json)
   PROMPT=$(cat prompts/prompt-$TASK_ID.md)
   echo "Task $TASK_ID"
   echo "$TASK" | jq -r '.thought'
   echo "---"
   echo "$PROMPT"
   ```

3. **Search prompts by keyword**:
   ```bash
   grep -l "Railway" prompts/*.md      # Find Railway tasks
   grep -l "database" prompts/*.md     # Find database tasks
   ```

## Benefits

- **Lean metadata**: `thoughts.json` stays focused on task tracking
- **Readable prompts**: Markdown files are searchable and editable
- **Separation of concerns**: Metadata ≠ execution guidance
- **Scalable**: Easy to add new tasks without bloating JSON

## Adding New Tasks

1. Add entry to `thoughts.json` (without prompt field)
2. Create `prompts/prompt-{id}.md` with full prompt
3. Commit both files

Example:
```bash
# thoughts.json
{
  "id": 22,
  "thought": "Your task description",
  "project": "project-name",
  "created": "2026-01-10T...",
  "status": "open",
  "resource": "C"
}

# prompts/prompt-22.md
[Full prompt text goes here]
```
