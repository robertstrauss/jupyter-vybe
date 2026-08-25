# Jupyter Vybe
## A fine-grained control vibe-coding tool in Juyter notebooks for researchers

## Overview
Use AI coding tools in your jupyter notebooks in a seamless way: add a prompt cell with [prompt shortcut], and execute it to have an agent generate a few cells with the desired code or propose edits to other cells. Also adds advanced autocompletion proposal ([autocomplete shortcut], behavior adjustable in settings) not just filling in the rest of a method call with more intelligent search, but proposing the relevant variables to enter or other pre/post processing lines, or even proposing restructuring the autocompleted line.
Fully backwards-copmatible; this uses normal notebook files and doesn't add any new or strange properties. Collaborators can open the same notebook and see the same thing.

[table of contents]

# Philosophy: Language Models as boilerplate compressors, not engineers

Vibe coding often becomes slop, where you don't know anything about the codebase and now have to use an AI agent to interact with it. Importantly, agents often fail in critical ways, hallucinating or doign things a completely unintuitive, inefficient, and confounded way, when entrusted with sole supervision of the codebase. However, they are incredibly fast at generating the boilerplate code and common patterns endemic to coding. How much of trying to write a new project is googling the exact syntax and documentation or copy-pasting already well-designed patterns from stack overflow, even if you're doing something very unique and original? Code is made up of repeatable blocks which AI are good at building, and are small and easily verifiable enough to be instantly double-checked by a human if put in front of them. This is the motivation behind jupyter vybe.

Jupyter vybe is intended to be more scientific, rigorous, and verifiable way of integrating the advantages of AI into software engineering, aimed at researchers. The philosophy is AI can be thought of as distillations of the data of code and language; trained on the entropy of language, they are compression/prediction algorithms optimized for language, and so are excellent for the kind of well documented, highly repeated patterns composing so much of code. In research one often wants to quickly validate or prototype a new idea, much of which involves reusing patterns to instantiate a model and implement many pre-made packages. This process could be massively accelerated by AI by predicting in small, controllable blocks. Jupyter is the perfect environment for this because its designed for research and prototyping, and already has this modular structure. Things don't become spaghetti, they become cells that you can immediately see output from individually, rather than integrating them into a massive codebase.

By being completely modular and only generating fine-grained, self-contained cells of code, *you* are still the engineer. You don't lose autonomy to an AI that you've deleagted your entire codebase to. The AI becomes a symbiotic tool, pulling common patterns and integrating well-documented and best practice patterns rather than putting you behind a prompt interface rather than an IDE. You don't get stuck only interacting with the codebase through a prompt interface as it gets so agent-generated and unfamiliar that you can't effectively cut through the bloat with your own eyes and an IDE.

## Usage

[Extension installation and enabling instructions]

### Inline Prompting
Once installed and enabled, the extension is triggered by adding a code cell and entering '> ' followed by a natural language prompt for cells to generate below this prompt cell or edits to propose throughout the notebook. The prompt is evaluated through the agent by running this code cell ('>' MUST be the first charachter in the cell input to be used as a prompt cell. Any natural language (or even example code/psuedo code/markdown text can follow within the cell input and wil be fed to the agent). Once evaluated with Shift+Enter or Cmd+Enter, the prompt will be fed to the agent, which will insert cells below the prompt cell only, or propose edits to other cells. If proposals are made, a status indicator in the top-right shows how many and clicking it or pressing the shortcut will take you to the first one, which can be accepted or rejected with the keyboard or buttons, bringing you to the next, and the next, until all are resolved and you are brought back to the prompt cell where you started. You can reverse all edits and cancel at any time with Ctrl+C, or undo individual edit approvals with Cmd+Z and be brought back to the previous one you approved. Once back at the prompt, Cmd+Z will undo all agent-inserted cells and dismiss all proposed edits and place your cursor back in the prompt cell.

### Prompt History

Clicking the icon in the right toolbar (below the settings and debugger icon) [HUMAN TODO: SCREENSHOT TOOLBAR W/ ICON] will open/close a prompt history pane where previous prompts and proposed cell inserts/edits can be scrolled through, from the latest at the bottom to the oldest within this session at the top. The history includes rejected edits. Clicking any proposed/edited cell in this history copies it to the cell clipboard so pressing 'v' in command mode pastes it as a cell below the current selection. Selecting an edit proposal/difference copies the net 'after' edited cell.  

### Settings

The settings pane, [CLAUDE: describe where to access the extension settings pane], contains the following controls:
- Extension master switch: a switch enabling/disabling the extension
- Agent selector: a dropdown menu selecting which agent executable to run (can be connected to local agents as well as remote agents [the remote agent executable should automatically log in])
- Context persistence: a switch enabling persistent message history (the agent remembers your previous prompts VS every prompt is a single-shot with no memmory, just the notebook and your prompt)
- A status indicator showing how full context is (including the notebook and tooling/instructions prompt given to the agnet)
- Trigger prefix: a text field where the prefix used at the beginning of a code cell to prompt the agent can be changed '>' is default. '!' and '?' are already used for bash or IPython help so can't be selected. Any valid code syntax can't be used (otherwise there would be ambiguity rather whether to actually evaluate it as code).
- Global prompt: a text field to modify a prompt the agent will always be given in addition to the notebook, tooling prompt, and other necessary context. Here you can describe properties/conventions you want all agent generated code to have, for example "well documented with concise phrases. Example usage in docstring. All caps for constants", or more meta information: "Never propose cell edits, only propose a new version of the function/class in a completley new cell."



## Agent Integration

### Agent tools
Agent can only add/edit cells of notebook, not directly change it (wire into the insert cell functionality of jupyter as a tool) to ensure proper formatting and that AI doesn't corrupt the notebook or change other things unwantedly.
Agent has add cell functionality only directly below prompt cell (up to a few cells), and global *propose* edit functionality (edits are not committed to other cells without user review).
Agent can also use IPython help ('?' + function/class/module to get docstring and syntax) and auto-completion (namespace searching) before returning proposed changes.
*IMPORTANT* Agent CANNOT directly execute anything through the kernel or send execution requests to the kernel; it may only modify the notebook and get information from IPython tools/autocomplete.

### Agent prompt
Agent has the full notebook in context, and all prompts start with a passage advising the agent to have its code additions be minimalist, simplistic, readable, and best-practice. Do not include example usage unless asked for explicitly, except in docstrings; Do not include any other context or output unless specificlaly requested by the user.

### Trigger
Prefix '>' (or custom editable in settings) interprets the rest of a code cell as a prompt (it can still include code or markdown, but this will be fed to the agent rather than executed). When executed (shift+enter, or default jupyter triggers) it sends the prompt to the agent, which is expected to reply with a few tool usages inserting cells below the prompt cell, and potentially proposing changes to other cells, accepted/rejected by the user. Prompt cells are converted to markdown after execution including the original prompt and any response outside tool usage, but the undo shortcut (Cmd+Z or system default) removes the agent's additions to the notebook, cancels all propsed edits, and converts it back to a prompt cell (with user input preserved) to be modified and re-executed by the user. 
