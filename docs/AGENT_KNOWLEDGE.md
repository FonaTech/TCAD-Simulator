# Agent And Knowledge Architecture

LLM and literature features are optional. The simulator should run without API keys or external model services.

## Knowledge Pipeline

The source file includes a local knowledge stack:

- `SemanticDocumentProcessor`: extracts sections, parameter triplets, tables, and process-relevant chunks from text.
- `LocalVectorIndex`: provides local token/hash based retrieval and optional persisted vector data.
- `KnowledgeEngine`: manages document ingestion, search, storage roots, and recipe extraction support.
- `ProcessMapper`: maps retrieved text or user intent to available `PROCESS_STEP_FACTORIES`.
- `PhysicsAuditor`: checks generated recipes for obvious parameter, material, and process-flow issues.

PDF ingestion is optional and depends on installed PDF libraries. Extracted data should be treated as local user data and stored under runtime storage, not committed.

## Recipe Assistance

Agent-assisted recipe design follows this path:

```text
User goal or paper text
    -> document chunks / retrieval
    -> process mapping
    -> candidate recipe JSON
    -> schema cleanup and material normalization
    -> physics audit
    -> optional trial simulation
    -> user review/apply
```

The final simulation still runs through the same `ProcessStep.execute(model)` protocol as hand-written recipes.

## Skills

The built-in TCAD skills system loads Markdown skill files with lightweight frontmatter. Skills can inject domain instructions into Agent prompts, but they should not bypass recipe schema validation or physics audit.

Important environment variables:

```bash
TCAD_SKILLS_DIR=/path/to/skills
TCAD_SKILLS_ENABLED=1
TCAD_SKILLS_MAX_SKILLS=3
TCAD_SKILLS_MAX_CHARS_PER_SKILL=6000
```

## Provider Configuration

Agent provider settings can come from WebUI/Admin config, local config files, or environment variables such as:

```bash
TCAD_AGENT_PROVIDER=openai
TCAD_AGENT_BASE_URL=https://example.invalid/v1
TCAD_AGENT_MODEL=model-name
TCAD_AGENT_API_KEY=...
TCAD_AGENT_TIMEOUT=60
```

Do not commit API keys, local provider configs, test configs, or private literature databases.

## Safety Boundary

Generated recipes are suggestions. The simulator performs schema cleanup and physics-inspired audits, but it cannot prove manufacturability. Treat Agent output as a draft that needs human review and validation against measured process data.
