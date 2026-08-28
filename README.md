# Jupyter Vybe: Human-in-the-Loop for High-Grade Vibe-Coding

Vibe coding can get messy ('slop') when not precisely controlled by a human. Jupyter notebook is the perfect interface to solve this for a few reasons:

1. Code visibility first. You prompt in the same notebook output code is displayed in, for immediate verification.
2. Modularity = fine-grained chunks of generated code, scoped to just one function or class at a time, with seperate output immediately visible. No more slop/spaghetti.
3. Outputs & errors bundled with the code as context. No separate routing of runtime errors to the agent.
4. Interactivity. The turn-based development environment of IPython lends itself to the nature of agents, while being extremely transparent to humans.

It boils down to having a **human in the loop** (with eyes on the actual code, _not just the chat prompt_), exactly what Jupyter was designed for.

[HUMAN: example use screenshots]

## Overview

Use AI coding tools in your jupyter notebooks in a seamless way: start a code cell with `>`, write what you want in plain language, and run the cell (Shift+Enter) to have an agent generate a few cells with the desired code or propose edits to other cells.

Also adds **inline completion**: ghost text from the same local model, filling in the rest of the line or block as you type. It is off by default (Settings ▸ Jupyter Vybe ▸ Inline completion) because it costs a model call at a keystroke cadence — on `qwen2.5-coder:7b` locally it answers in well under a second.

Fully backwards-copmatible; this uses normal notebook files and doesn't add any new or strange properties. Collaborators can open the same notebook and see the same thing.

**Contents**

- [Philosophy: Language Models as boilerplate compressors, not engineers](#philosophy-language-models-as-boilerplate-compressors-not-engineers)
- [Usage](#usage)
  - [Installation](#installation)
  - [Inline Prompting](#inline-prompting)
  - [Edit Proposal Reviewal](#edit-proposal-reviewal)
  - [Prompt History](#prompt-history)
  - [Settings](#settings)
- [Agent Integration](#agent-integration)
  - [Connect your own agent](#connect-your-own-agent)
  - [Agent tools](#agent-tools)
  - [Agent prompt](#agent-prompt)
  - [Trigger](#trigger)

# Philosophy: Language Models as boilerplate compressors, not engineers

How often do you open google or stack overflow while coding to grab a commonplace pattern or check syntax? Let the agent do the repetitive stuff while you focus on being an engineer or researcher, and prototyping your idea. In many domains, research especially, you _need_ to know how the codebase works, and that its not doing something else than what you want to test under the hood. For knowledge production, _knowing_ what is being tested is the whole point; satisfying a win condition is not enough.

Generative AI-based coding often becomes slop, where you don't know anything about the codebase and now have to use an AI agent to interact with it. Importantly, agents often fail in critical ways, hallucinating or doing things a completely unintuitive, inefficient, and confounded way, when entrusted with sole supervision of the codebase. However, they are incredibly fast at generating the boilerplate code and common patterns endemic to coding. How much of trying to write a new project is googling the exact syntax and documentation or copy-pasting already well-designed patterns from stack overflow, even if you're doing something very unique and original? Code is made up of repeatable blocks which AI are good at building, and are small and easily verifiable enough to be instantly double-checked by a human if put in front of them. This is the motivation behind jupyter vybe.

LLMs are scored on the principle of cross-entropy; they can be thought of as compression algorithms optimized for language and code, and so are perfect for reproducing the kind of well documented, highly repeated patterns endemic to programming.

By being completely modular and only generating fine-grained, self-contained cells of code, _you_ are still the engineer. You don't lose autonomy to an AI that you delagte an increasing amount of your codebase to. The AI becomes a symbiotic tool, pulling on common patterns and integrating well-documented best practice patterns rather than putting you behind the chatting prompt interface and away from the IDE. You don't get stuck only interacting with the codebase through an AI chat as it becomes an increasingly agent-generated, unfamiliar, hallucination-riddled black-box, too bloated for a human to get a grasp on.

## Usage

### Installation

```bash
pip install jupyter-vybe
```

That is the whole installation: the wheel carries both halves — the JupyterLab
frontend and the Jupyter Server extension — and both enable themselves. Restart
JupyterLab and confirm with:

```bash
jupyter labextension list   # jupyter-vybe v0.1.0 enabled OK
jupyter server extension list   # jupyter_vybe enabled
```

You need **JupyterLab 4.4 or newer** and Python 3.9+.

You also need a model for it to talk to. The default is
[Ollama](https://ollama.com) running `qwen2.5-coder:7b` on the same machine:

```bash
ollama pull qwen2.5-coder:7b
```

Nothing else needs configuring — if Ollama is running, the extension finds it.
For any other agent, see [Connect your own agent](#connect-your-own-agent).

If anything seems not to work, run:

```bash
jupyter-vybe-doctor
```

It checks both halves of the install, whether the settings schema is where
JupyterLab looks for it, and which agents actually answer — naming the machine
that answered, which matters if you are tunnelling to a model elsewhere. Every
check in it exists because that specific thing went wrong at least once.

To disable temporarily without uninstalling, turn off the master switch in
settings; the extension then hands every cell straight back to Jupyter.

**From source**, for development:

```bash
pip install -e ".[dev]"
jupyter labextension develop --overwrite .
jlpm watch          # rebuilds the frontend on save
```

Note that a Python change needs the server restarted — an editable install does
not hot-reload modules. `./scripts/dev.sh` starts a lab that prints the commit
it is serving and refuses to start beside a stale one, which exists because
several hours were once lost to testing against a server that predated the fix.

### Inline Prompting

Once installed and enabled, the extension is triggered by adding a code cell and entering '>' followed by a natural language prompt for cells to generate below this prompt cell or edits to propose throughout the notebook. The prompt is evaluated through the agent by running this code cell ('>' MUST be the first charachter in the cell input to be used as a prompt cell. Any natural language (or even example code/psuedo code/markdown text can follow within the cell input and wil be fed to the agent). Once evaluated with Shift+Enter or Cmd+Enter, the prompt will be fed to the agent, which will insert cells below the prompt cell only, or propose edits to other cells. If proposals are made, a status indicator in the top-right shows how many and clicking it or pressing the shortcut will take you to the first one, which can be accepted or rejected with the keyboard or buttons, bringing you to the next, and the next, until all are resolved and you are brought back to the prompt cell where you started. You can reverse all edits and cancel at any time with Ctrl+C, or undo individual edit approvals with Cmd+Z and be brought back to the previous one you approved. Once back at the prompt, Cmd+Z will undo all agent-inserted cells and dismiss all proposed edits and place your cursor back in the prompt cell.

[HUMAN: SCREENSHOT]

### Edit Proposal Reviewal

The agent can directly add cells immediately below the prompt (which you can instantly see and veto), but to modify any other part of the notebook it needs human reviewal. Proposed cell edits are shown like a git diff, green for new lines, red for replaced lines. If the model decides it needs to change other cells, a bubble in the top-right of the notebook panel shows you how many attempted edits there are, and takes you to them one at a time when clicked (or keyboard shortcut pressed). `Y` accepts the new version and moves to the next cell to review, `N` rejects modifications, `Return` places your cursor in the cell to modify it as you wish and continue reviewing once you hit `Esc`. You can also reject all edits at any time or go back undoing your approvals with `Cmd+Z`.

[HUMAN: REVIEWAL PROCESS SCREENSHOT]

### Prompt History

Clicking the icon in the right toolbar (below the settings and debugger icon) [HUMAN TODO: SCREENSHOT TOOLBAR W/ ICON] will open/close a prompt history pane where previous prompts and proposed cell inserts/edits can be scrolled through, from the latest at the bottom to the oldest within this session at the top. The history includes rejected edits. Clicking any proposed/edited cell in this history copies it to the cell clipboard so pressing 'v' in command mode pastes it as a cell below the current selection. Selecting an edit proposal/difference copies the net 'after' edited cell.

[HUMAN: PROPMT HISTORY SCREENSHOT]

### Settings

The settings pane is at **Settings ▸ Settings Editor ▸ Jupyter Vybe** (the same
place as every other JupyterLab extension's settings), or through the command
palette with `Jupyter Vybe: Open Settings`. Every control is also editable as
raw JSON through the Advanced JSON editor if you prefer. It contains the
following controls:

- Extension master switch: a switch enabling/disabling the extension
- Agent selector: a dropdown menu selecting which agent executable to run (can be connected to local agents as well as remote agents [the remote agent executable should automatically log in])
- Context persistence: a switch enabling persistent message history (the agent remembers your previous prompts VS every prompt is a single-shot with no memmory, just the notebook and your prompt)
- A status indicator showing how full context is (including the notebook and tooling/instructions prompt given to the agnet)
- Trigger prefix: a text field where the prefix used at the beginning of a code cell to prompt the agent can be changed '>' is default. '!' and '?' are already used for bash or IPython help so can't be selected. Any valid code syntax can't be used (otherwise there would be ambiguity rather whether to actually evaluate it as code).
- Global prompt: a text field to modify a prompt the agent will always be given in addition to the notebook, tooling prompt, and other necessary context. Here you can describe properties/conventions you want all agent generated code to have, for example "well documented with concise phrases. Example usage in docstring. All caps for constants", or more meta information: "Never propose cell edits, only propose a new version of the function/class in a completley new cell."

[HUMAN: SCREENSHOT]

## Agent Integration

### Connect your own agent

The AI engine is completely interchangeable. Hook it up to a local agent or your subscription AI service of choice. Just go to settings, open the model selection drop down, and click 'add model' and follow the setup steps.

[HUMAN: SCREENSHOTS]

I personally recommend using a local model, if you have a device that can support it. The whole design of this project scopes the agent to fine-grained, simple, modular chunks of generated code, which open source local models are plenty adept at especially in Python.

[HUMAN: SUMMARIZE/LINK TO INSTALLING LOCAL MODEL]

### Agent tools

- _IMPORTANT_ Agent CANNOT directly execute anything through the kernel or send execution requests to the kernel; it may only modify the notebook and get information from IPython tools/autocomplete.
- Agent can only add cells to notebook directly below prompt cell, or propose changes tht must be human-reviewed elsewhere. It cannot directly change the notebook arbitrarily to ensure proper formatting and that AI doesn't corrupt the notebook or change other things unwantedly.
- Agent has add cell functionality only directly below prompt cell (up to a few cells), and global _propose_ edit functionality (edits are not committed to other cells without user review).
- Agent can also use IPython help ('?' + function/class/module to get docstring and syntax) and auto-completion (namespace searching) before returning proposed changes.

### Agent prompt

Agent has the full notebook in context, and all prompts start with a passage advising the agent to have its code additions be minimalist, simplistic, readable, and best-practice. Do not include example usage unless asked for explicitly, except in docstrings; Do not include any other context or output unless specificlaly requested by the user.

### Trigger

Prefix '>' (or custom editable in settings) interprets the rest of a code cell as a prompt (it can still include code or markdown, but this will be fed to the agent rather than executed). When 'executed' (shift+enter, or default jupyter triggers) it sends the prompt to the agent, which is expected to reply with a few tool usages inserting cells below the prompt cell, and potentially proposing changes to other cells, accepted/rejected by the user. Prompt cells are converted to markdown after execution including the original prompt and any response outside tool usage, but the undo shortcut (Cmd+Z or system default) removes the agent's additions to the notebook, cancels all propsed edits, and converts it back to a prompt cell (with user input preserved) to be modified and re-executed by the user.
