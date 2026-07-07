---
name: skill-creator
description: Use when the user wants to create, update, or review a new opencode skill that extends the agent with specialized knowledge, workflows, or tool integrations. Guides through the SKILL.md format, frontmatter, description writing, and best practices.
---

# Skill Creator

This skill helps you create, update, and review opencode skills.

## What is a skill

A skill is a markdown file that extends opencode with specialized knowledge,
workflows, or tool integrations. Skills are loaded at startup and their
instructions become available to the agent when the task matches the skill's
description.

## File location

Skills are placed in:

```
.opencode/skills/<skill-name>/SKILL.md
```

The folder name must match the `name` in frontmatter exactly.

Global skills live in `~/.config/opencode/skills/<name>/SKILL.md`.

## SKILL.md format

Every SKILL.md has two parts: **YAML frontmatter** and a **markdown body**.

### Frontmatter

```yaml
---
name: skill-name                  # required, lowercase hyphen-separated, ≤64 chars, matches folder name
description: Short description.   # required — what the skill does AND when to trigger it
license: MIT                      # optional
compatibility: opencode>=1.0      # optional
metadata:                         # optional, string-string map
  version: "1.0"
---
```

### Body

The body is standard markdown. It becomes the skill's instructions. Write clear,
actionable guidance: what to do, how to do it, what to avoid, and when to stop.

## Writing a good description

The `description` is the **only** field opencode uses to decide whether to load
the skill for a given task. It must cover two things concisely:

1. **What** this skill does (domain, capability)
2. **When** to trigger it (keywords, filenames, scenarios)

**Good examples:**

```yaml
description: Use when creating or editing opencode skills. Covers SKILL.md format, frontmatter fields, description writing, and file placement conventions.
```

```yaml
description: Use ONLY when working with the auth_agent project's Python execution subsystem (prototype/stage2/). Covers goal-loop architecture, playbook tables, failure classification, and Stage A-F conventions.
```

**Bad examples:**

```yaml
description: Helps with skills.          # too vague, no trigger keywords
description: I am a skill for skills.    # first-person, no trigger keywords
```

Rules:
- Write in **third person** ("Use when..." not "I help with...")
- Front-load **concrete trigger keywords** and **filenames**
- Use `Use ONLY when...` to gate narrowly scoped skills

## Skill body conventions

1. **Start with a clear heading** (`# Skill Name`) that matches the skill name.
2. **Explain the domain**: what problems this skill solves and what context it
   assumes.
3. **Provide step-by-step workflows** where applicable. Use numbered lists for
   sequential steps, bullet points for reference material.
4. **Include examples**: show good vs bad, templates to fill in, or worked
   scenarios.
5. **Reference existing files**: point to real paths in the project that the
   skill user will need. Use relative paths when the skill is project-scoped.
6. **Define boundaries**: explicitly say what this skill does NOT cover to
   prevent over-triggering.
7. **Keep it concise**: skills are injected into agent context. Avoid
   unnecessary prose.

## Creating a new skill — workflow

1. **Choose a name**: lowercase, hyphen-separated, descriptive. Examples:
   `python-executor`, `stage2-goal-loop`, `pr-review`.

2. **Create the directory**:
   ```
   .opencode/skills/<skill-name>/
   ```

3. **Write SKILL.md** with frontmatter and body following the conventions
   above.

4. **Review the description** against the trigger criteria — will it load
   when the right task comes up? Will it stay quiet on unrelated topics?

5. **Test**: restart opencode, then ask a question that should trigger the
   skill. Verify the agent uses the skill's instructions.

6. **Iterate**: adjust the description if the skill triggers too often or not
   at all. Skills are not hot-reloaded, so restart opencode after each change.

## Updating an existing skill

1. Read the current SKILL.md.
2. Make targeted edits that preserve existing conventions.
3. If the skill scope changes, update the description.
4. After saving, tell the user to restart opencode.

## Common pitfalls

| Pitfall | Fix |
|---------|-----|
| Description too broad — skill loads on unrelated tasks | Add `Use ONLY when...` gate and narrow keywords |
| Description too narrow — skill never triggers | Add broader trigger keywords (filenames, domain terms) |
| Skill body too long | Split reference material into separate files, reference them from the skill |
| Skill references stale paths | Always verify paths exist in the current codebase before writing |
| Frontmatter keys misspelled | Valid keys: `name`, `description`, `license`, `compatibility`, `metadata` |
| Name doesn't match folder name | The `name` field must exactly match the folder name |

## Template

Copy this as a starting point for new skills:

```markdown
---
name: my-skill
description: Use when [scenario]. Covers [key topics]. Reference [key files].
---

# My Skill

## What this skill covers

[One paragraph explaining the domain and scope.]

## Prerequisites

- [Knowledge or access needed before using this skill.]
- [Key files or directories the user should be aware of.]

## Workflow

1. [First step]
2. [Second step]
3. [Third step]

## Conventions

- [Rule 1]
- [Rule 2]

## Boundaries

This skill does NOT cover:
- [Excluded topic 1]
- [Excluded topic 2]

## References

- [`path/to/relevant/file.py`](path/to/relevant/file.py) — [what it contains]
- [`docs/some-doc.md`](docs/some-doc.md) — [what it covers]
```
