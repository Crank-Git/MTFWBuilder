---
paths:
  - "docs/**/*.md"
  - "*.md"
  - ".claude/**/*.md"
  - "mtfwbuilder/**/*.py"
  - "tests/**/*.py"
  - "templates/**/*.html"
  - "static/js/**/*.js"
---

# Writing standard (STE)

Every spec file, issue body, comment, manual page and **code comment** in this project
uses Simplified Technical English.

`README.md` is the one exception. It uses the voice of the owner, and US English still
applies. Every other Markdown file, including each page in `docs/`, keeps this standard.

The project's controlled vocabulary is the `## Terms`
table in `docs/specs/spec.md`. Read it before you write a domain word. If you need a word
that is not in the table, add it to the table.

## Never rewrite into STE — reproduce verbatim

- Quotes from a person: user answers, review comments, dictated requirements.
- Evidence: error messages, logs, console output, test output, stack traces, `file:line`.
- Code, config, commands, JSON, file paths, identifiers, label names.
- Third-party product names and API field names (for example `USERPREFS_*` keys).
- UI copy inside mockups.

## Rules

Sentences:
1. A procedure sentence is 20 words or fewer. A description sentence is 25 or fewer.
2. One instruction per sentence.
3. One topic per paragraph. Six sentences maximum.
4. Put the condition first, then the action.
5. Put the warning before the step it applies to.

Words:
6. One word, one meaning, one part of speech.
7. One concept, one word. Never rotate synonyms.
8. Keep noun clusters to three words.
9. No metaphor, idiom, or slang. State the mechanism.
10. No abbreviation the Terms table does not define.

Grammar:
11. Active voice.
12. Present tense for behaviour. Imperative for instructions.
13. Keep the articles.
14. No `-ing` form as a noun or as a heading.
15. Write positively. Keep a prohibition only when the wrong action is likely and costly.
16. Use a vertical list when a sentence carries more than two conditions or parameters.

## Patterns

- **Functional requirement:** one testable statement, active voice, present tense, no
  conjunction. Not "should be able to". No vague quantity.
- **Acceptance criterion:** one observable result, not an implementation, never "works
  correctly".
- **Issue title:** imperative verb, one deliverable, 10 words or fewer.
- **Issue body:** `## Context`, `## What to build` (numbered steps), `## Acceptance
  criteria` (checkboxes), `## Out of scope`. One kind of information per heading.
- **Manual step:** second person, present tense, one action per step.
- **Code comment:** one sentence, one fact, active voice. Say why, not what. Name the
  reason a reader cannot see.
  - Bad: `# increment the counter`
  - Good: `# Upstream removes all text after // on a line, so a URL value breaks.`
- **Docstring:** open with one sentence that states the result. Then parameters, then
  failure modes.
- **Test name:** one behaviour, present tense: `test_failed_build_scrubs_userprefs_file`.
- **Marker comment:** keep `TODO` / `FIXME` / `HACK`, write the body in STE, and name the
  issue: `# TODO(#412): Replace the fixed timeout with the setting.`
- **PM decision comment:** the decision, then the reason. One sentence each.

## Check before you write the file

- [ ] No sentence over 25 words. No instruction over 20.
- [ ] One instruction per step.
- [ ] Condition before action. Warning before step.
- [ ] Every domain word is in the Terms table, with one meaning.
- [ ] No synonym rotation.
- [ ] No noun cluster over three words.
- [ ] No metaphor, idiom, or undefined abbreviation.
- [ ] Active voice, present tense, articles present, no `-ing` nouns or headings.
- [ ] Lists for anything with more than two conditions or parameters.
- [ ] Quotes, evidence, code, paths and identifiers verbatim.
- [ ] Code comments state the reason. Docstrings open with the result. Markers carry an
      issue number.
