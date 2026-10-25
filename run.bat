@echo off
cd /d "%~dp0"
uv run --locked python -m qq_agent_harness --config config/harness.toml
