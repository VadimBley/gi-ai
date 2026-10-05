Ĝi (gi-ai) - a small, private AI assistant that runs on your own computer.

Quick start
  1. Install a local model runtime (Ollama) and pull a model:
       ollama pull qwen3:4b
  2. Check that everything works:
       gi health
  3. Ask something:
       gi ask "What is the capital of Slovakia?"
     Add --verbose to see token counts and speed on standard error:
       gi ask --verbose "What is the capital of Slovakia?"
  4. Create your private workspace and record a task:
       gi init-workspace
       gi task new --type bug --title "Answer was cut off"
       gi task list
  5. Check your local security baseline:
       gi selfcheck

Using LM Studio
  Ĝi can also use LM Studio (version 0.4.0 or newer) through its native REST API.
  In ~/.config/gi-ai/gi.toml:
       [llm]
       backend = "lmstudio"
       endpoint = "http://127.0.0.1:1234"
       model = "qwen/qwen3.5-9b"
  When LM Studio runs on the Windows host and Ĝi runs in WSL, use
       endpoint = "http://@gateway:1234"
       allow_remote = true
  "@gateway" is replaced on every run by the default-route gateway address.
  gi health shows the address it used, whether the model is available and loaded,
  and the names of any reply fields Ĝi does not know yet.
  Every chat is stateless: LM Studio is told not to store the conversation.
  Ĝi gives the model no tools and refuses replies that try to call one.

API token
  If LM Studio requires an API token, either set the environment variable
  GI_AI_LLM_TOKEN, or put the token in a file that only you can read:
       chmod 600 ~/.config/gi-ai/lmstudio.token
  and set llm.token_file = "~/.config/gi-ai/lmstudio.token". The variable wins
  over the file. Ĝi sends the token only to the configured endpoint and never
  prints or stores it.

Privacy
  Ĝi talks only to a model on this computer (127.0.0.1) unless you change
  llm.allow_remote in ~/.config/gi-ai/gi.toml. Even then gi selfcheck fails if the
  endpoint is not loopback or a private network address. Ĝi ignores proxy settings
  and never follows redirects, so nothing is sent anywhere else.
  Your workspace (~/.local/share/gi-ai/workspace) is readable only by you.

More: man gi-ai
