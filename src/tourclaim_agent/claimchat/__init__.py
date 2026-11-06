"""Port of the old in-browser Claim Assistant (Tour_React src/lib/claimChatbot/, src/lib/ocr/).

One module per TypeScript file, with the TypeScript names in snake_case. Claim state and rows are plain dicts with
the TypeScript field names, because they travel as JSON between the React form and the agent. `js_compat` holds the
JavaScript semantics (Number(), String(), truthiness, the browser's date parser, en-IN formatting) the port relies on.
See Project-Context/agent/old_assistant_parity/00_port_plan.md.
"""
