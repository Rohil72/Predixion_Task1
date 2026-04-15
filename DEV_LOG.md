# Dev Log

## Commit 1: Minimal Safe LLM Setup
- Added `.gitignore`, `.env`, `call_llm.py`, and `run_llm.ps1`
- Goal: create a safe, functional local OpenRouter test harness

## Commit 2: Research Report Schema
- Added `research_report.schema.json`
- Goal: define the report contract before orchestration

## Commit 3: Formatter Agent
- Added `formatter_input.schema.json`, `formatter_result.schema.json`, and `formatter_agent.py`
- Goal: normalize upstream output into `report + diagnostics`

## Commit 4: Raw Formatter Input
- Updated `formatter_input.schema.json` and `formatter_agent.py`
- Goal: make the formatter accept raw upstream model output directly

## Commit 5: Document Renderer
- Added `renderer/Skills.md`, `renderer/render_report.py`, and `renderer/serve_reports.py`
- Updated `.gitignore` and `run_llm.ps1`
- Goal: render reports as localhost research documents and keep immutable snapshots

## Commit 6: Single-Agent Research Loop
- Added `research_agent.py` and `research_agent_prompt.md`
- Updated `.env` and `run_llm.ps1`
- Goal: replace one-shot raw chat with a bounded Tavily-backed researcher

## Commit 7: Document-Native Report Contract
- Updated `research_report.schema.json`, `formatter_agent.py`, `research_agent.py`, `research_agent_prompt.md`, `renderer/render_report.py`, and `renderer/Skills.md`
- Goal: move from a flat answer contract to a structured corporate research document
