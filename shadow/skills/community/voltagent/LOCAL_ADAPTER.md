# Shadow adapter: development agent guides

The accompanying MIT-licensed agent files are specialist review and implementation guides, not independent running processes. Development Studio uses one configured AI provider per job and supplies repository source selected for that job. Apply only the parts of a guide relevant to the owner's objective and the supplied files.

The upstream `tools`, `model`, handoff, and other-agent instructions describe a different host. Here the development model cannot run Bash, browse, install dependencies, call other agents, or deploy. It must not claim those actions occurred. Audit tasks report grounded findings without edits; build tasks produce reviewable patches. Preserve Shadow's authentication, account isolation, Telegram permissions, and secret handling.

Source: https://github.com/VoltAgent/awesome-claude-code-subagents/tree/721e9734670bfaf7283194e234ebb88e94c82dcd
