@echo off
rem Start the JARVES Core (Python). Set OPENAI_API_KEY first for AI + live voice; without it JARVES runs offline.
rem Logs: %LOCALAPPDATA%\jarves\logs\jarves.log   Status: uv --directory core run jarves status
uv --directory "%~dp0." run jarves run %*
