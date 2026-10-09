# Shadow adapter

Use this skill for a new external-service integration or MCP server. First inspect this project's existing FastAPI routes, provider configuration, and `shadow/integrations/` contracts; extend those patterns where they fit. Use the bundled best-practices and Python references as guidance. Keep credentials in server-side settings, validate remote inputs and outputs, set timeouts, and return actionable errors. Do not invent API behavior, expose secrets, install packages, run commands, or claim a service is connected unless the project actually has that capability.

Upstream: https://github.com/anthropics/skills/tree/683bc88e56f3e09ba94f7055977f3d3aa499f202/skills/mcp-builder
