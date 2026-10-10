# Community skills installed for Shadow

The upstream `SKILL.md` files and their per-skill `LICENSE.txt` files are retained in `anthropics/`. They come from Anthropic's skills repository at commit `683bc88e56f3e09ba94f7055977f3d3aa499f202`; the selected skills are Apache-2.0 licensed. `LOCAL_ADAPTER.md` files add Shadow-specific routing and capability notes; upstream files were not edited.

Installed skills: `frontend-design`, `mcp-builder` with its Python and best-practices references, `skill-creator` with its schema reference, and `webapp-testing`. The older `openai/skills` repository identifies itself as deprecated; these focused upstream prompts are model-independent and can be used with both configured OpenAI and Claude providers.

Additional curated resources:

- Anthropic `theme-factory` at commit `dbd4588f9e1033efb41dad4bef2f7947c8993d44`, including its ten text palettes, Apache-2.0 `LICENSE.txt`, and a Shadow-specific adapter. It is offered to design tasks and theme/color requests. The PDF showcase and artifact scripts are not installed.
- Seven specialist guides from VoltAgent `awesome-claude-code-subagents` at commit `721e9734670bfaf7283194e234ebb88e94c82dcd`: API design, frontend, architecture, code review, performance, security, and test automation. The repository's MIT `LICENSE` is retained. Development Studio routes matching tasks to these guides with `voltagent/LOCAL_ADAPTER.md`.

The guides add task-specific instructions to the existing AI workflow. They do not install executable agents, grant extra tools or API access, or run as separate background services. Upstream snapshots are read-only to Development Studio; change a `LOCAL_ADAPTER.md` or a local skill for Shadow-specific behavior. New upstream snapshots should be reviewed and pinned before updating.
