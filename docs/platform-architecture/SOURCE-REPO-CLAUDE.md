# CLAUDE.md - System Architecture & Agent Handoff Guidelines

## Repository Purpose
This repository serves as the definitive source of truth for our system architecture, data models, infrastructure scale constraints, and cross-component integration patterns. 

We practice **Architecture-as-Code**. Every design decision, data schema, and workflow topology must be documented as version-controlled Markdown or text-based diagrams (Mermaid.js).

---

## 1. System Design Principles & Constraints
When reviewing or writing architecture specs, Claude must strictly enforce:
*   **Horizontal Scalability & Bottleneck Identification:** Always analyze state management, hot spots, lock contention, and network overhead. Assume high throughput requirements.
*   **Observability First:** Every component design must explicitly detail its logging topology, tracing headers (OpenTelemetry compatible), and critical performance metrics.
*   **Fail-Safe Isolation:** Enforce bulkhead patterns, circuit breakers, and explicit error-handling states for all inter-service communications.
*   **Pragmatism Over Gold-Plating:** Keep components decoupled but avoid microservice sprawl unless bounded contexts explicitly justify it.

---

## 2. Document Architecture & Style Guide
All files in `architecture/` must maintain strict uniformity to maximize readability for both humans and downstream LLMs.

### Directory Mapping
*   `/architecture/` - Component designs, data flows, core subsystem breakdowns.
*   `/diagrams/` - Plain text `.mermaid` files or inline Markdown blocks.
*   `/tasks/` - Standalone context packages and execution specs for downstream agents (e.g., Gemini).

### Formatting Requirements
*   **Headers:** Use sequential Markdown headers (`#`, `##`, `###`). Avoid skipping levels.
*   **Data Models:** Use explicit Markdown tables defining Type, Nullable, Indexing Strategy, and Scale/Retention notes.
*   **Diagrams:** Use native **Mermaid.js** syntax exclusively. Keep flowcharts top-to-bottom (`graph TD`) or left-to-right (`graph LR`). Do not use third-party visual layout tags.

---

## 3. Downstream Agent Handoff Protocol (`tasks/`)
When instructed to "create a handoff task for Gemini" or another implementation agent, Claude must generate a self-contained Markdown file inside the `/tasks/` directory using the following exact template.

### Handoff File Naming Convention
`YYYYMMDD_[target_agent]_[short_feature_name].md` (e.g., `20260710_gemini_db_repository_layer.md`)

### Handoff Structure Requirement
Every handoff file must contain these exact root-level headers:

1.  **# Task Context & System Summary:** A brief explanation of where this task fits into the broader architecture.
2.  **# Upstream Specifications:** Direct references or absolute copy-pastes of the relevant schemas, constraints, and architecture blocks from `/architecture/`. *The downstream agent must not be forced to guess context.*
3.  **# Explicit Instruction Prompt:** A clearly bounded prompt explicitly written for a text/code generation LLM. Use declarative commands: "Write...", "Generate...", "Implement...".
4.  **# Acceptance Criteria:** A concrete bulleted list of functional requirements, test scenarios, and performance/security guardrails that the output must satisfy.
5.  **# Expected Output Schema/Format:** Define whether the agent should return raw SQL scripts, a specific directory of boilerplate code, or a structured JSON response.

---

## 4. Operational Command Reference
Frequently used commands for validating documentation syntax and managing orchestrations locally:

*   **Format Check:** `npm run lint:md` or local markdown lint commands.
*   **Mermaid Validation:** `mmdc -i diagrams/sample.mermaid -o /dev/null` (if mermaid-cli is installed locally).
*   **Task Dispatching:** `python scripts/dispatch_tasks.py` (for automated ingestion by local LLM APIs or runners like Prefect).
*   **Git Sync:** `git add . && git commit -m "feat(arch): update architecture definitions" && git push`

## 5. Session Interaction Guardrails
*   **Do not hallucinate files:** If an architecture piece is missing, ask or leave an explicit `TODO: [Owner]` marker.
*   **Be direct & analytical:** Skip conversational filler. Dive straight into identifying race conditions, database indexing gaps, or single points of failure.
*   **Preserve Context:** When editing an existing architecture file, do not wipe out historical rationale blocks unless explicitly directed.

## 6. Model Orchestration Guardrails
*   **Planning & Architecture:** Use the active Fable 5 session to execute the `brainstorm` and `writing-plans` skills. Do not write implementation code in this state.
*   **Execution & Subagents:** When initiating the `subagent-driven-development` or `test-driven-development` execution loops, explicitly instruct the platform to spawn **Opus** worker agents to execute the individual markdown tasks.
